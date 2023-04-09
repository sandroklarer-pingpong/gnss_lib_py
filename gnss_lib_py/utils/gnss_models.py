"""Model GNSS measurements and measurement corrections.

Functions to generate expected measurements, simulate noisy mesurements,
and estimate pseudorange corrections (clock corrections, ionospheric and
tropospheric delays) for given receiver states and time.
"""

__authors__ = "Ashwin Kanhere, Bradley Collicott"
__date__ = "17 Jan, 2023"

import warnings

import numpy as np
from numpy.random import default_rng

import gnss_lib_py.utils.constants as consts
from gnss_lib_py.utils.coordinates import ecef_to_geodetic, ecef_to_el_az
from gnss_lib_py.parsers.navdata import NavData
from gnss_lib_py.utils.time_conversions import gps_millis_to_tow
from gnss_lib_py.utils.sv_models import _find_visible_ephem, _extract_pos_vel_arr, \
                        _find_sv_location, find_sv_states, _compute_eccentric_anomaly, \
                        _find_visible_sv_posvel, _sort_ephem_measures, \
                        _filter_ephemeris_measurements


def add_measures(measurements, ephemeris_path, iono_params=None,
                 pseudorange=True, doppler=True, corrections=True,
                 delta_t_dec = -2):
    """Estimate measurements and add to given measurements

    Parameters
    ----------
    measurements : gnss_lib_py.parsers.navdata.NavData
        Received measurements for which SV states are required. Must
        contain `gps_millis`, `gnss_id`, and `sv_id` fields.
    ephemeris_path : string
        Location where ephemeris files are stored. Files will be
        downloaded if they don't exist for the given date and constellation.
    iono_params : np.ndarray
        Parameters to calculate the ionospheric delay in pseudoranges.
    pseudorange : bool
        Flag on whether pseudoranges are to be calculated and used or not.
    doppler : bool
        Flag on whether doppler measurements are to be calculated and
        used or not.
    corrections : bool
        Flag on whether pseudorange corrections are to be calculated and
        used or not.
    delta_t_dec : int
        Decimal places after which times are considered as belonging to
        the same discrete time interval.
    """
    constellations = np.unique(measurements['gnss_id'])
    measurements, ephem = _filter_ephemeris_measurements(measurements,
                                         constellations, ephemeris_path)
    info_rows = ['gps_millis', 'gnss_id', 'sv_id']
    sv_state_rows = ['x_sv_m', 'y_sv_m', 'z_sv_m', 'vx_sv_mps', 'vy_sv_mps', 'vz_sv_mps']
    rx_pos_rows_to_find = ['x_rx*_m', 'y_rx*_m', 'z_rx*_m']
    rx_pos_rows_idxs = measurements.find_wildcard_indexes(
                                            rx_pos_rows_to_find,
                                            max_allow=1)
    rx_pos_rows = [rx_pos_rows_idxs['x_rx*_m'][0],
                   rx_pos_rows_idxs['y_rx*_m'][0],
                   rx_pos_rows_idxs['z_rx*_m'][0]]
    # velocity rows
    rx_vel_rows_to_find = ['vx_rx*_mps', 'vy_rx*_mps', 'vz_rx*_mps']
    # clock rows
    rx_clk_rows_to_find = ['b_rx*_m', 'b_dot_rx*_mps']

    # Check if SV states exist, if they don't, add them
    est_measurements = NavData()
    # Loop through the measurement file per time step
    for gps_millis, _, measure_frame in measurements.loop_time('gps_millis',
                                                        delta_t_decimals=delta_t_dec):
        # Sort the satellites
        rx_ephem, sorted_sats_ind, inv_sort_order = _sort_ephem_measures(measure_frame, ephem)
        # Create new NavData with SV positions and velocities
        # If they're not given, the SV states computed with measures will be used
        try:
            measure_frame.in_rows(sv_state_rows)
            use_posvel = False
            sv_posvel = NavData()
            for row in sv_state_rows:
                sv_posvel[row] = measure_frame[row]
            for row in info_rows:
                sv_posvel[row] = measure_frame[row]
            # sv_posvel = sv_posvel.sort(ind=sorted_sats_ind)
            sv_posvel = sv_posvel.sort(ind=sorted_sats_ind)
        except KeyError:
            sv_posvel = None
            use_posvel = True

        # Extract RX states into State NavData
        state = NavData()
        for row in rx_pos_rows:
            state[row] = measure_frame[row, 0]
        # velocity and clock rows
        vel_clk_rows = rx_vel_rows_to_find + rx_clk_rows_to_find
        for row in vel_clk_rows:
            try:
                row_idx = measure_frame.find_wildcard_indexes(row,max_allow=1)
                state[row_idx[row][0]] = measure_frame[row_idx[row][0], 0]
            except KeyError:
                warnings.warn("Assuming 0 "+ row + " for Rx", RuntimeWarning)
                state[row] = 0

        # Compute measurements
        if pseudorange or doppler:
            est_meas, sv_posvel = expected_measures(gps_millis, state,
                                                    ephem=rx_ephem,
                                                    sv_posvel=sv_posvel)
            # Reverse the sorting to match the input measurements
            est_meas = est_meas.sort(ind=inv_sort_order)
        else:
            est_meas = None
        if corrections:
            est_clk, est_trp, est_iono = calculate_pseudorange_corr(gps_millis,
                                        state=state, ephem=rx_ephem,
                                        sv_posvel=sv_posvel, iono_params=iono_params)
            # Reverse the sorting to match the input measurements
            est_clk = est_clk[inv_sort_order]
            est_trp = est_trp[inv_sort_order]
            est_iono = est_iono[inv_sort_order]
        else:
            est_clk = None
            est_trp = None
            est_iono = None
        # Add required values to new rows
        if sv_posvel is not None:
            # Reverse the sorting to match the input measurements
            sv_posvel = sv_posvel.sort(ind=sorted_sats_ind)
        est_frame = NavData()
        if pseudorange:
            est_frame['est_pr_m'] = est_meas['est_pr_m']
        if doppler:
            est_frame['est_doppler_hz'] = est_meas['est_doppler_hz']
        if corrections:
            est_frame['b_sv_m'] = est_clk
            est_frame['tropo_delay_m'] = est_trp
            est_frame['iono_delay_m'] = est_iono
        if use_posvel:
            for row in sv_state_rows:
                est_frame[row] = sv_posvel[row]
        if len(est_measurements)==0:
            est_measurements = est_frame
        else:
            est_measurements.concat(est_frame, inplace=True)
    est_measurements = measurements.concat(est_measurements, axis=0, inplace=False)
    return est_measurements


def simulate_measures(gps_millis, state, noise_dict=None, ephem=None,
                      sv_posvel=None, rng=None, el_mask=5.):
    """Simulate GNSS pseudoranges and doppler measurements given receiver state.

    Measurements are simulated by finding satellites visible from the
    given position (in state), computing the expected pseudorange and
    doppler measurements for those satellites, corresponding to the given
    state, and adding Gaussian noise to these expected measurements.

    Parameters
    ----------
    gps_millis : int
        Time at which measurements are needed, measured in milliseconds
        since start of GPS epoch [ms].
    state : gnss_lib_py.parsers.navdata.NavData
        NavData instance containing state i.e. 3D position, 3D velocity,
        receiver clock bias and receiver clock drift rate at which
        measurements have to be simulated.
        Must be a single state (single column)
    noise_dict : dict
        Dictionary with pseudorange ('prange_sigma') and doppler noise
        ('doppler_sigma') standard deviation values in [m] and [m/s].
        If None, uses default values `prange_sigma=6` and
        `doppler_sigma=1`.
    ephem : gnss_lib_py.parsers.navdata.NavData
        NavData instance containing satellite ephemeris parameters for a
        particular time of ephemeris. Use None if not available and using
        SV positions directly instead.
    sv_posvel : gnss_lib_py.parsers.navdata.NavData
        Precomputed positions of satellites, set to None if not available.
    rng : np.random.Generator
        A random number generator for sampling random noise values.
    el_mask: float
        The elevation mask above which satellites are considered visible
        from the given receiver position. Only visible sate.

    Returns
    -------
    measurements : gnss_lib_py.parsers.navdata.NavData
        Pseudorange (label: `prange`) and doppler (label: `doppler`)
        measurements with satellite SV. Gaussian noise is added to
        expected measurements to simulate stochasticity.
    sv_posvel : gnss_lib_py.parsers.navdata.NavData
        Satellite positions and velocities (same as input if provided).

    """
    #TODO: Verify the default noise value for doppler range
    #Handle default values
    if rng is None:
        rng = default_rng()

    if noise_dict is None:
        noise_dict = {}
        noise_dict['prange_sigma'] = 6.
        noise_dict['doppler_sigma'] = 1.

    rx_ecef, _, _, _ = _extract_state_variables(state)

    if ephem is not None:
        ephem = _find_visible_ephem(gps_millis, rx_ecef, ephem, el_mask=el_mask)
        sv_posvel = None
    else:
        sv_posvel = _find_visible_sv_posvel(gps_millis, rx_ecef, sv_posvel, el_mask=el_mask)
        ephem = None

    measurements, sv_posvel = expected_measures(gps_millis, state,
                                                ephem, sv_posvel)
    num_svs   = len(measurements)

    # Create simulated measurements that match received naming convention
    #TODO: Add clock and atmospheric delays here
    measurements['raw_pr_m']  = (measurements['est_pr_m']
        + noise_dict['prange_sigma'] *rng.standard_normal(num_svs))

    measurements['doppler_hz'] = (measurements['est_doppler_hz']
        + noise_dict['doppler_sigma']*rng.standard_normal(num_svs))

    # Remove expected measurements so they can be added later
    measurements.remove(rows=['est_pr_m', 'est_doppler_hz'], inplace=True)

    return measurements, sv_posvel


def expected_measures(gps_millis, state, ephem=None, sv_posvel=None):
    """Compute expected pseudoranges and doppler measurements given receiver
    states.

    Parameters
    ----------
    gps_millis : int
        Time at which measurements are needed, measured in milliseconds
        since start of GPS epoch [ms].
    state : gnss_lib_py.parsers.navdata.NavData
        NavData instance containing state i.e. 3D position, 3D velocity,
        receiver clock bias and receiver clock drift rate at which
        measurements have to be simulated.
        Must be a single state (single column)
    ephem : gnss_lib_py.parsers.navdata.NavData
        NavData instance containing satellite ephemeris parameters for a
        particular time of ephemeris, use None if not available and
        using position directly.
    sv_posvel : gnss_lib_py.parsers.navdata.NavData
        Precomputed positions of satellites (if available).

    Returns
    -------
    measurements : gnss_lib_py.parsers.navdata.NavData
        Pseudorange (label: `prange`) and doppler (label: `doppler`)
        measurements with satellite SV. Also contains SVs and gps_tow at
        which the measurements are simulated.
    sv_posvel : gnss_lib_py.parsers.navdata.NavData
        Satellite positions and velocities (same as input if provided).
    """
    # and satellite positions in sv_posvel
    rx_ecef, rx_v_ecef, clk_bias, clk_drift = _extract_state_variables(state)
    sv_posvel, del_pos, true_range = _find_sv_location(gps_millis,
                                                         rx_ecef, ephem, sv_posvel)
    # sv_pos, sv_vel, del_pos are both Nx3
    _, sv_vel = _extract_pos_vel_arr(sv_posvel)

    # Obtain corrected pseudoranges and add receiver clock bias to them
    prange = true_range
    prange += clk_bias
    # prange = (correct_pseudorange(gps_week, gps_tow ephem, true_range,
    #                              np.reshape(pos, [-1, 3])) + bias)
    # TODO: Correction should be applied to the received pseudoranges, not
    # modelled/expected pseudorange -- per discussion in meeting on 11/12
    # TODO:  corrections instead of returning corrected pseudoranges
    # Obtain difference of velocity between satellite and receiver

    del_vel = sv_vel - np.tile(np.reshape(rx_v_ecef, [3,1]), [1, len(sv_posvel)])
    prange_rate = np.sum(del_vel*del_pos, axis=0)/true_range
    prange_rate += clk_drift
    # Remove the hardcoded F1 below and change to frequency in measurements
    doppler = -(consts.F1/consts.C) * (prange_rate)
    measurements = NavData()
    measurements['sv_id'] = sv_posvel['sv_id']
    measurements['gnss_id'] = sv_posvel['gnss_id']
    measurements['est_pr_m'] = prange
    measurements['est_doppler_hz'] = doppler
    return measurements, sv_posvel


def _extract_state_variables(state):
    """Extract position, velocity and clock bias terms from state.

    Parameters
    ----------
    state : gnss_lib_py.parsers.navdata.NavData
        NavData containing state values i.e. 3D position, 3D velocity,
        receiver clock bias and receiver clock drift rate at which
        measurements will be simulated.

    Returns
    -------
    rx_ecef : np.ndarray
        3x1 Receiver 3D ECEF position [m].
    rx_v_ecef : np.ndarray
        3x1 Receiver 3D ECEF velocity.
    clk_bias : float
        Receiver clock bais [m].
    clk_drift : float
        Receiver clock drift [m/s].

    """
    assert len(state)==1, "Only single state accepted for GNSS simulation"

    rx_idxs = state.find_wildcard_indexes(['x_rx*_m',
                                           'y_rx*_m',
                                           'z_rx*_m',
                                           'vx_rx*_mps',
                                           'vy_rx*_mps',
                                           'vz_rx*_mps',
                                           'b_rx*_m',
                                           'b_dot_rx*_mps',
                                           ],
                                           max_allow=1)

    rx_ecef = np.reshape(state[[rx_idxs['x_rx*_m'][0],
                                rx_idxs['y_rx*_m'][0],
                                rx_idxs['z_rx*_m'][0]]], [3,1])
    rx_v_ecef = np.reshape(state[[rx_idxs['vx_rx*_mps'][0],
                                  rx_idxs['vy_rx*_mps'][0],
                                  rx_idxs['vz_rx*_mps'][0]]], [3,1])
    clk_bias = state[rx_idxs['b_rx*_m'][0]]
    clk_drift = state[rx_idxs['b_dot_rx*_mps'][0]]
    return rx_ecef, rx_v_ecef, clk_bias, clk_drift


def calculate_pseudorange_corr(gps_millis, state=None, ephem=None, sv_posvel=None,
                                 iono_params=None):
    """Incorporate corrections in measurements.

    Incorporate clock corrections (relativistic and polynomial), tropospheric
    and ionospheric atmospheric delay corrections.

    Parameters
    ----------
    gps_millis : int
        Time at which measurements are needed, measured in milliseconds
        since start of GPS epoch [ms].
    ephem : gnss_lib_py.parsers.navdata.NavData
        Satellite ephemeris parameters for measurement SVs, use None if
        using satellite positions instead.
    sv_posvel : gnss_lib_py.parsers.navdata.NavData
        Precomputed positions of satellites corresponding to the input
        `gps_millis`, set to None if not available.
    iono_params : np.ndarray
        Ionospheric atmospheric delay parameters for Klobuchar model,
        passed in 2x4 array, use None if not available.
    rx_ecef : np.ndarray
        3x1 receiver position in ECEF frame of reference [m], use None
        if not available.

    Returns
    -------
    clock_corr : np.ndarray
        Satellite clock corrections [m].
    iono_delay : np.ndarray
        Estimated delay caused by the ionosphere [m].
    tropo_delay : np.ndarray
        Estimated delay caused by the troposhere [m].

    Notes
    -----
    Based on code written by J. Makela.
    AE 456, Global Navigation Sat Systems, University of Illinois
    Urbana-Champaign. Fall 2017

    """

    if state is not None:
        rx_ecef, _, _, _ = _extract_state_variables(state)
    else:
        rx_ecef = None


    if ephem is not None:
        satellites = len(ephem)
    else:
        assert sv_posvel is not None, \
                "SV states must be given when ephemeris isn't"
        satellites = len(sv_posvel)

    # calculate clock pseudorange correction
    if ephem is not None:
        clock_corr, _, _ = _calculate_clock_delay(gps_millis, ephem)
    else:
        warnings.warn("Broadcast ephemeris not given, returning 0 "\
                    + "clock corrections", RuntimeWarning)
        clock_corr = np.zeros(satellites)

    if rx_ecef is not None:
        # Calculate the tropospheric delays
        tropo_delay = _calculate_tropo_delay(gps_millis, rx_ecef, ephem, sv_posvel)
    else:
        warnings.warn("Receiver position not given, returning 0 "\
                    + "ionospheric delay", RuntimeWarning)
        tropo_delay = np.zeros(satellites)

    if iono_params is not None and rx_ecef is not None:
        iono_delay = _calculate_iono_delay(gps_millis, iono_params,
                                            rx_ecef, ephem, sv_posvel)
    else:
        warnings.warn("Ionospheric delay parameters or receiver position"\
                    + "not given, returning 0 ionospheric delay", \
                        RuntimeWarning)
        iono_delay = np.zeros(satellites)

    return clock_corr, tropo_delay, iono_delay


def _calculate_clock_delay(gps_millis, ephem):
    """Calculate the modelled satellite clock delay

    Parameters
    ---------
    gps_millis : int
        Time at which measurements are needed, measured in milliseconds
        since start of GPS epoch [ms].
    ephem : gnss_lib_py.parsers.navdata.NavData
        Satellite ephemeris parameters for measurement SVs.

    Returns
    -------
    clock_corr : np.ndarray
        Satellite clock corrections containing all terms [m].
    corr_polynomial : np.ndarray
        Polynomial clock perturbation terms [m].
    clock_relativistic : np.ndarray
        Relativistic clock correction terms [m].

    """
    # Extract required GPS constants
    ecc        = ephem['e']     # eccentricity
    sqrt_sma = ephem['sqrtA'] # sqrt of semi-major axis

    # if np.abs(delta_t).any() > 302400:
    #     delta_t = delta_t - np.sign(delta_t)*604800

    gps_week, gps_tow = gps_millis_to_tow(gps_millis)

    # Compute Eccentric Anomaly
    ecc_anom = _compute_eccentric_anomaly(gps_week, gps_tow, ephem)

    # Determine pseudorange corrections due to satellite clock corrections.
    # Calculate time offset from satellite reference time
    t_offset = gps_tow - ephem['t_oc']
    if np.abs(t_offset).any() > 302400:  # pragma: no cover
        t_offset = t_offset-np.sign(t_offset)*604800

    # Calculate clock corrections from the polynomial corrections in
    # broadcast message
    corr_polynomial = (ephem['SVclockBias']
                     + ephem['SVclockDrift']*t_offset
                     + ephem['SVclockDriftRate']*t_offset**2)

    # Calcualte the relativistic clock correction
    corr_relativistic = consts.F * ecc * sqrt_sma * np.sin(ecc_anom)

    # Calculate the total clock correction including the Tgd term
    clk_corr = (corr_polynomial - ephem['TGD'] + corr_relativistic)

    #Convert values to equivalent meters from seconds
    clk_corr = consts.C*clk_corr
    corr_polynomial = consts.C*corr_polynomial
    corr_relativistic = consts.C*corr_relativistic

    return clk_corr, corr_polynomial, corr_relativistic


def _calculate_tropo_delay(gps_millis, rx_ecef, ephem=None, sv_posvel=None):
    """Calculate tropospheric delay

    Parameters
    ----------
    gps_millis : int
        Time at which measurements are needed, measured in milliseconds
        since start of GPS epoch [ms].
    ephem : gnss_lib_py.parsers.navdata.NavData
        Satellite ephemeris parameters for measurement SVs.
    rx_ecef : np.ndarray
        3x1 array of ECEF rx_pos position [m].
    sv_posvel : gnss_lib_py.parsers.navdata.NavData
        Precomputed positions of satellites, set to None if not available.

    Returns
    -------
    tropo_delay : np.ndarray
        Tropospheric corrections to pseudorange measurements [m].

    Notes
    -----
    Based on code written by J. Makela.
    AE 456, Global Navigation Sat Systems, University of Illinois
    Urbana-Champaign. Fall 2017

    """
    # Make sure that receiver position is 3x1
    rx_ecef = np.reshape(rx_ecef, [3,1])

    # Determine the satellite locations
    if sv_posvel is None:
        assert ephem is not None, "Must provide ephemeris or positions" \
                        + " to find troposphere delay"
        sv_posvel = find_sv_states(gps_millis, ephem)
    sv_pos, _ = _extract_pos_vel_arr(sv_posvel)

    # compute elevation and azimuth
    el_az = ecef_to_el_az(rx_ecef, sv_pos)
    el_r  = np.deg2rad(el_az[0, :])

    # Calculate the WGS-84 latitude/longitude of the receiver
    rx_lla = ecef_to_geodetic(rx_ecef)
    height = rx_lla[2, :]

    # Force height to be positive
    ind = np.argwhere(height < 0).flatten()
    if len(ind) > 0:  # pragma: no cover
        height[ind] = 0

    # Calculate the delay
    tropo_delay = consts.TROPO_DELAY_C1/(np.sin(el_r)+consts.TROPO_DELAY_C2) \
                     * np.exp(-height*consts.TROPO_DELAY_C3)/consts.C

    # Convert tropospheric delaly in equivalent meters
    tropo_delay = consts.C*tropo_delay
    return tropo_delay


def _calculate_iono_delay(gps_millis, iono_params, rx_ecef, ephem=None, sv_posvel=None):
    """Calculate the ionospheric delay in pseudorange using the Klobuchar
    model Section 5.3.2 [1]_.

    Parameters
    ----------
    gps_millis : int
        Time at which measurements are needed, measured in milliseconds
        since start of GPS epoch [ms].
    iono_params : np.ndarray
        Ionospheric atmospheric delay parameters for Klobuchar model,
        passed in 2x4 array, use None if not available.
    rx_ecef : np.ndarray
        3x1 receiver position in ECEF frame of reference [m], use None
        if not available.
    ephem : gnss_lib_py.parsers.navdata.NavData
        Satellite ephemeris parameters for measurement SVs, use None if
        using satellite positions instead.
    sv_posvel : gnss_lib_py.parsers.navdata.NavData
        Precomputed positions of satellites corresponding to the input
        `gps_millis`, set to None if not available.

    Returns
    -------
    iono_delay : np.ndarray
        Estimated delay caused by the ionosphere [m].

    Notes
    -----
    Based on code written by J. Makela.
    AE 456, Global Navigation Sat Systems, University of Illinois
    Urbana-Champaign. Fall 2017

    References
    ----------
    ..  [1] Misra, P. and Enge, P,
        "Global Positioning System: Signals, Measurements, and Performance."
        2nd Edition, Ganga-Jamuna Press, 2006.

    """
    _, gps_tow = gps_millis_to_tow(gps_millis)

    #Reshape receiver position to 3x1
    rx_ecef = np.reshape(rx_ecef, [3,1])

    # Determine the satellite locations
    if sv_posvel is None:
        assert ephem is not None, "Must provide ephemeris or positions" \
                                + " to find visible satellites"
        sv_posvel = find_sv_states(gps_millis, ephem)
    sv_pos, _ = _extract_pos_vel_arr(sv_posvel)
    el_az = ecef_to_el_az(rx_ecef, sv_pos)
    el_r = np.deg2rad(el_az[0, :])
    az_r = np.deg2rad(el_az[1, :])

    # Calculate the WGS-84 latitude/longitude of the receiver
    wgs_llh = ecef_to_geodetic(rx_ecef)
    lat_r = np.deg2rad(wgs_llh[0, :])
    lon_r = np.deg2rad(wgs_llh[1, :])

    # Parse the ionospheric parameters
    alpha = iono_params[0,:]
    beta = iono_params[1,:]

    # Calculate the psi angle
    psi = 0.1356/(el_r+0.346) - 0.0691

    # Calculate the ionospheric geodetic latitude
    lat_i = lat_r + psi * np.cos(az_r)

    # Make sure values are in bounds
    ind = np.argwhere(np.abs(lat_i) > 1.3090)
    if len(ind) > 0:
        lat_i[ind] = 1.3090 * np.sign(lat_i[ind])  # pragma: no cover
    # Calculate the ionospheric geodetic longitude
    lon_i = lon_r + psi * np.sin(az_r)/np.cos(lat_i)

    # Calculate the solar time corresponding to the gps_tow
    solar_time = 1.3751e4 * lon_i + gps_tow

    # Make sure values are in bounds
    solar_time = np.mod(solar_time,86400)

    # Calculate the geomagnetic latitude (semi-circles)
    lat_m = (lat_i + 2.02e-1 * np.cos(lon_i - 5.08))/np.pi
    # Calculate the period
    period = beta[0]+beta[1]*lat_m+beta[2]*lat_m**2+beta[3]*lat_m**3

    # Make sure values are in bounds
    ind = np.argwhere(period < 72000).flatten()
    if len(ind) > 0:
        period[ind] = 72000  # pragma: no cover

    # Calculate the local time angle
    theta = 2*np.pi*(solar_time - 50400) / period

    # Calculate the amplitude term
    amp = (alpha[0]+alpha[1]*lat_m+alpha[2]*lat_m**2+alpha[3]*lat_m**3)

    # Make sure values are in bounds
    ind = np.argwhere(amp < 0).flatten()
    if len(ind) > 0:
        amp[ind] = 0  # pragma: no cover

    # Calculate the slant factor
    slant_fact = 1.0 + 5.16e-1 * (1.6755-el_r)**3

    # Calculate the ionospheric delay
    iono_delay = slant_fact * 5.0e-9
    ind = np.argwhere(np.abs(theta) < np.pi/2.).flatten()
    if len(ind) > 0:
        iono_delay[ind] = slant_fact[ind]* \
            (5e-9+amp[ind]*(1-theta[ind]**2/2.+theta[ind]**4/24.))

    # Convert ionospheric delay to equivalent meters
    iono_delay = consts.C*iono_delay
    return iono_delay
