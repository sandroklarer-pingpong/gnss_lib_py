"""Generates expected measurements and simulates pseudoranges.

Functions to generate expected measurements and to simulate pseudoranges
and doppler for GPS satellites.

"""

__authors__ = "Ashwin Kanhere, Bradley Collicott"
__date__ = "16 July 2021"

import os
import sys
import numpy as np
import pandas as pd
from numpy.random import default_rng

# append <path>/gnss_lib_py/gnss_lib_py/ to path
sys.path.append(os.path.dirname(
                os.path.dirname(
                os.path.realpath(__file__))))
from core.constants import GPSConsts
from core.coordinates import ecef2geodetic

# TODO: Check if any of the functions are sorting the dataframe w.r.t SV while
# processing the measurements


def _extract_pos_vel_arr(sat_posvel):
    """Extract satellite positions and velocities into numpy arrays.

    Parameters
    ----------
    sat_posvel : pd.DataFrame
        Dataframe with satellite states

    Returns
    -------
    prns : List
        Satellite PRNs in input DataFrame
    sat_pos : ndarray
        ECEF satellite positions
    sat_vel : ndarray
        ECEF satellite x, y and z velocities
    """
    prns   = [int(prn[1:]) for prn in sat_posvel.index]
    sat_pos = sat_posvel.filter(['x', 'y', 'z'])
    sat_vel   = sat_posvel.filter(['vx', 'vy', 'vz'])
    sat_pos = sat_pos.to_numpy()
    sat_vel   = sat_vel.to_numpy()
    return prns, sat_pos, sat_vel
    # TODO: Remove prns from function output if not needed

def simulate_measures(gpsweek, gpstime, ephem, pos, bias, b_dot, vel,
                      prange_sigma = 6., doppler_sigma=0.1, sat_posvel=None):
    """Simulate GNSS pseudoranges and doppler measurements given receiver state.

    Measurements are simulated by adding Gaussian noise to measurements expected
    based on the receiver states.

    Parameters
    ----------
    gpsweek : int
        Week in GPS calendar
    gpstime : float
        GPS time of the week for simulate measurements [s]
    ephem : pd.DataFrame
        DataFrame containing all satellite ephemeris parameters for gpsweek and
        gpstime
    pos : ndarray
        1x3 Receiver 3D ECEF position [m]
    bias : float
        Receiver clock bais [m]
    b_dot : float
        Receiver clock drift [m/s]
    vel : ndarray
        1x3 Receiver 3D ECEF velocity
    prange_sigma : float
        Standard deviation of Gaussian error in simulated pseduranges
    doppler_sigma : float
        Standard deviation of Gaussian error in simulated doppler measurements
    sat_posvel : pd.DataFrame
        Precomputed positions of satellites (if available)

    Returns
    -------
    measurements : pd.DataFrame
        Pseudorange and doppler measurements indexed by satellite SV with
        Gaussian noise
    sat_posvel : pd.DataFrame
        Satellite positions and velocities (same as input if provided)

    """
    #TODO: Modify to work with input satellite positions
    #TODO: Add assertions/error handling for sizes of position, bias, b_dot and
    # velocity arrays
    #TODO: Modify to use single state vector instead of multiple inputs
    #TODO: Modify to use single dictionary with uncertainty values
    ephem = _find_visible_sats(gpsweek, gpstime, pos, ephem)
    measurements, sat_posvel = expected_measures(gpsweek, gpstime, ephem, pos,
                                              bias, b_dot, vel, sat_posvel)
    M   = len(measurements.index)
    rng = default_rng()

    measurements['prange']  = (measurements['prange']
        + prange_sigma *rng.standard_normal(M))

    measurements['doppler'] = (measurements['doppler']
        + doppler_sigma*rng.standard_normal(M))

    return measurements, sat_posvel

def expected_measures(gpsweek, gpstime, ephem, pos,
                      bias, b_dot, vel, sat_posvel=None):
    """Compute expected pseudoranges and doppler measurements given receiver
    states.

    Parameters
    ----------
    gpsweek : int
        Week in GPS calendar
    gpstime : float
        GPS time of the week for simulate measurements [s]
    ephem : pd.DataFrame
        DataFrame containing all satellite ephemeris parameters for gpsweek and
        gpstime
    pos : ndarray
        1x3 Receiver 3D ECEF position [m]
    bias : float
        Receiver clock bais [m]
    b_dot : float
        Receiver clock drift [m/s]
    vel : ndarray
        1x3 Receiver 3D ECEF velocity
    sat_posvel : pd.DataFrame
        Precomputed positions of satellites (if available)

    Returns
    -------
    measurements : pd.DataFrame
        Expected pseudorange and doppler measurements indexed by satellite SV
    sat_posvel : pd.DataFrame
        Satellite positions and velocities (same as input if provided)
    """
    # NOTE: When using saved data, pass saved DataFrame with ephemeris in ephem
    # and satellite positions in sat_posvel
    # TODO: Modify this function to use PRNS from measurement in addition to
    # gpstime from measurement
    pos = np.reshape(pos, [1, 3])
    vel = np.reshape(vel, [1, 3])
    gpsconsts = GPSConsts()
    sat_posvel, del_pos, true_range = _find_sat_location(gpsweek, gpstime,
                                                         ephem, pos, sat_posvel)
    # sat_pos, sat_vel, del_pos are both Nx3
    _, _, sat_vel = _extract_pos_vel_arr(sat_posvel)

    # Obtain corrected pseudoranges and add receiver clock bias to them
    prange = true_range + bias
    # prange = (correct_pseudorange(gpstime, gpsweek, ephem, true_range,
    #                              np.reshape(pos, [-1, 3])) + bias)
    # TODO: Correction should be applied to the received pseudoranges, not
    # modelled/expected pseudorange -- per discussion in meeting on 11/12
    # TODO: Add corrections instead of returning corrected pseudoranges

    # Obtain difference of velocity between satellite and receiver
    del_vel = sat_vel - np.tile(np.reshape(vel, 3), [len(ephem), 1])
    prange_rate = np.sum(del_vel*del_pos, axis=1)/true_range + b_dot
    doppler = -(gpsconsts.F1/gpsconsts.C) * (prange_rate)
    # doppler = pd.DataFrame(doppler, index=prange.index.copy())
    measurements = pd.DataFrame(np.column_stack((prange, doppler)),
                                index=sat_posvel.index,
                                columns=['prange', 'doppler'])
    return measurements, sat_posvel


def _find_visible_sats(gpsweek, gpstime, rx_ecef, ephem, el_mask=5.):
    """Trim input ephemeris to keep only visible SVs.

    Parameters
    ----------
    gpsweek : int
        Week in GPS calendar
    gpstime : float
        GPS time of the week for simulate measurements [s]
    rx_ecef : ndarray
        1x3 row rx_pos ECEF position vector [m]
    ephem  pd.DataFrame
        DataFrame containing all satellite ephemeris parameters for gpsweek and
        gpstime
    el_mask : float
        Minimum elevation of returned satellites

    Returns
    -------
    eph : pd.DataFrame
        Ephemeris parameters of visible satellites

    """
    gpsconsts = GPSConsts()
    # Find positions and velocities of all satellites
    approx_posvel = find_sat(ephem, gpstime - gpsconsts.T_TRANS, gpsweek)
    # Find elevation and azimuth angles for all satellites
    _, approx_pos, _ = _extract_pos_vel_arr(approx_posvel)
    approx_el_az = find_elaz(np.reshape(rx_ecef, [1, 3]), approx_pos)
    # Keep attributes of only those satellites which are visible
    keep_ind = approx_el_az[:,0] > el_mask
    # prns = approx_posvel.index.to_numpy()[keep_ind]
    # TODO: Remove above statement if superfluous
    # TODO: Check that a copy of the ephemeris is being generated, also if it is
    # needed
    eph = ephem.loc[keep_ind, :]
    return eph


def _find_sat_location(gpsweek, gpstime, ephem, pos, sat_posvel=None):
    """Return satellite positions, difference from rx_pos position and ranges.

    Parameters
    ----------
    gpsweek : int
        Week in GPS calendar
    gpstime : float
        GPS time of the week for simulate measurements [s]
    ephem : pd.DataFrame
        DataFrame containing all satellite ephemeris parameters for gpsweek and
        gpstime
    pos : ndarray
        1x3 Receiver 3D ECEF position [m]
    sat_posvel : pd.DataFrame
        Precomputed positions of satellites (if available)

    Returns
    -------
    sat_posvel : pd.DataFrame
        Satellite position and velocities (same if input)
    del_pos : ndarray
        Difference between satellite positions and receiver position
    true_range : ndarray
        Distance between satellite and receiver positions

    """
    gpsconsts = GPSConsts()
    pos = np.reshape(pos, [1, 3])
    if sat_posvel is None:
        satellites = len(ephem.index)
        sat_posvel = find_sat(ephem, gpstime - gpsconsts.T_TRANS, gpsweek)
        del_pos, true_range = _find_delxyz_range(sat_posvel, pos, satellites)
        t_corr = true_range/gpsconsts.C
        # Find satellite locations at (a more accurate) time of transmission
        sat_posvel = find_sat(ephem, gpstime-t_corr, gpsweek)
    else:
        satellites = len(sat_posvel.index)
    del_pos, true_range = _find_delxyz_range(sat_posvel, pos, satellites)
    t_corr = true_range/gpsconsts.C
    # Corrections for the rotation of the Earth during transmission
    # _, sat_pos, sat_vel = _extract_pos_vel_arr(sat_posvel)
    del_x = gpsconsts.OMEGAEDOT*sat_posvel['x'] * t_corr
    del_y = gpsconsts.OMEGAEDOT*sat_posvel['y'] * t_corr
    sat_posvel['x'] = sat_posvel['x'] + del_x
    sat_posvel['y'] = sat_posvel['y'] + del_y
    return sat_posvel, del_pos, true_range


def _find_delxyz_range(sat_posvel, pos, satellites):
    """Return difference of satellite and rx_pos positions and range between them.

    Parameters
    ----------
    sat_posvel : pd.DataFrame
        Satellite position and velocities
    pos : ndarray
        1x3 Receiver 3D ECEF position [m]
    satellites : int
        Number of satellites in sat_posvel

    Returns
    -------
    del_pos : ndarray
        Difference between satellite positions and receiver position
    true_range : ndarray
        Distance between satellite and receiver positions
    """
    # Repeating computation in find_sat_location
    #NOTE: Input is from satellite finding in AE 456 code
    pos = np.reshape(pos, [1, 3])
    if np.size(pos)!=3:
        raise ValueError('Position is not in XYZ')
    _, sat_pos, _ = _extract_pos_vel_arr(sat_posvel)
    del_pos = sat_pos - np.tile(np.reshape(pos, [-1, 3]), (satellites, 1))
    true_range = np.linalg.norm(del_pos, axis=1)
    return del_pos, true_range


def find_sat(ephem, times, gpsweek):
    """Compute position and velocities for all satellites in ephemeris file
    given time of clock.

    Parameters
    ----------
    ephem : pd.DataFrame
        DataFrame containing ephemeris parameters of satellies for which states
        are required
    times : ndarray
        GPS time of the week at which positions are required [s]
    gpsweek : int
        Week of GPS calendar corresponding to time of clock

    Returns
    -------
    sat_posvel : pd.DataFrame
        DataFrame indexed by satellite SV containing positions and velocities

    Notes
    -----
    Based on code written by J. Makela.
    AE 456, Global Navigation Sat Systems, University of Illinois
    Urbana-Champaign. Fall 2017

    Satellite velocity calculations based on algorithms introduced in [1]_.

    References
    ----------
    ..  [1] B. F. Thompson, S. W. Lewis, S. A. Brown, and T. M. Scott,
        “Computing GPS satellite velocity and acceleration from the broadcast
        navigation message,” NAVIGATION, vol. 66, no. 4, pp. 769–779, Dec. 2019,
        doi: 10.1002/navi.342.

    """
    # Satloc contains both positions and velocities.

    # Load in GPS constants
    gpsconsts = GPSConsts()

    # Extract parameters
    c_is = ephem['C_is']
    c_ic = ephem['C_ic']
    c_rs = ephem['C_rs']
    c_rc = ephem['C_rc']
    c_uc = ephem['C_uc']
    c_us = ephem['C_us']
    M_0  = ephem['M_0']
    dN   = ephem['deltaN']

    e        = ephem['e']     # eccentricity
    omega    = ephem['omega'] # argument of perigee
    omega_0  = ephem['Omega_0']
    sqrt_sma = ephem['sqrtA'] # sqrt of semi-major axis
    sma      = sqrt_sma**2      # semi-major axis

    sqrt_mu_A = np.sqrt(gpsconsts.MUEARTH) * sqrt_sma**-3 # mean angular motion
    gpsweek_diff = np.mod(gpsweek,1024) - np.mod(ephem['GPSWeek'],1024)*604800.

    # if np.size(times_all)==1:
    #     times_all = times_all*np.ones(len(ephem))
    # else:
    #     times_all = np.reshape(times_all, len(ephem))
    # times = times_all
    sat_posvel = pd.DataFrame()
    sat_posvel.loc[:,'sv'] = ephem.index
    sat_posvel.set_index('sv', inplace=True)
    #TODO: Check if 'dt' or 'times' should be stored in the final DataFrame
    sat_posvel.loc[:,'times'] = times

    dt = times - ephem['t_oe'] + gpsweek_diff

    # Calculate the mean anomaly with corrections
    M_corr = dN * dt
    M = M_0 + (sqrt_mu_A * dt) + M_corr

    # Compute Eccentric Anomaly
    E = _compute_eccentric_anomoly(M, e, tol=1e-5)

    cos_E   = np.cos(E)
    sin_E   = np.sin(E)
    e_cos_E = (1 - e*cos_E)

    # Calculate the true anomaly from the eccentric anomaly
    sin_nu = np.sqrt(1 - e**2) * (sin_E/e_cos_E)
    cos_nu = (cos_E-e) / e_cos_E
    nu     = np.arctan2(sin_nu, cos_nu)

    # Calcualte the argument of latitude iteratively
    phi_0 = nu + omega
    phi   = phi_0
    for i in range(5):
        cos_to_phi = np.cos(2.*phi)
        sin_to_phi = np.sin(2.*phi)
        phi_corr = c_uc * cos_to_phi + c_us * sin_to_phi
        phi = phi_0 + phi_corr

    # Calculate the longitude of ascending node with correction
    omega_corr = ephem['OmegaDot'] * dt

    # Also correct for the rotation since the beginning of the GPS week for
    # which the Omega0 is defined.  Correct for GPS week rollovers.
    omega = omega_0 - (gpsconsts.OMEGAEDOT*(times + gpsweek_diff)) + omega_corr

    # Calculate orbital radius with correction
    r_corr = c_rc * cos_to_phi + c_rs * sin_to_phi
    r      = sma*e_cos_E + r_corr

    ############################################
    ######  Lines added for velocity (1)  ######
    ############################################
    dE   = (sqrt_mu_A + dN) / e_cos_E
    dphi = np.sqrt(1 - e**2)*dE / e_cos_E
    # Changed from the paper
    dr   = (sma * e * dE * sin_E) + 2*(c_rs*cos_to_phi - c_rc*sin_to_phi)*dphi

    # Calculate the inclination with correction
    i_corr = c_ic*cos_to_phi + c_is*sin_to_phi + ephem['IDOT']*dt
    i = ephem['i_0'] + i_corr

    ############################################
    ######  Lines added for velocity (2)  ######
    ############################################
    di = 2*(c_is*cos_to_phi - c_ic*sin_to_phi)*dphi + ephem['IDOT']

    # Find the position in the orbital plane
    xp = r*np.cos(phi)
    yp = r*np.sin(phi)

    ############################################
    ######  Lines added for velocity (3)  ######
    ############################################
    du = (1 + 2*(c_us * cos_to_phi - c_uc*sin_to_phi))*dphi
    dxp = dr*np.cos(phi) - r*np.sin(phi)*du
    dyp = dr*np.sin(phi) + r*np.cos(phi)*du
    # Find satellite position in ECEF coordinates
    cos_omega = np.cos(omega)
    sin_omega = np.sin(omega)
    cos_i = np.cos(i)
    sin_i = np.sin(i)

    sat_posvel.loc[:,'x'] = xp*cos_omega - yp*cos_i*sin_omega
    sat_posvel.loc[:,'y'] = xp*sin_omega + yp*cos_i*cos_omega
    sat_posvel.loc[:,'z'] = yp*sin_i
    # TODO: Add satellite clock bias here using the 'clock corrections' not to
    # be used but compared against SP3 and Android data

    ############################################
    ######  Lines added for velocity (4)  ######
    ############################################
    omega_dot = ephem['OmegaDot'] - gpsconsts.OMEGAEDOT
    sat_posvel.loc[:,'vx'] = (dxp * cos_omega
                         - dyp * cos_i*sin_omega
                         + yp  * sin_omega*sin_i*di
                         - (xp * sin_omega + yp*cos_i*cos_omega)*omega_dot)

    sat_posvel.loc[:,'vy'] = (dxp * sin_omega
                         + dyp * cos_i * cos_omega
                         - yp  * sin_i * cos_omega * di
                         + (xp * cos_omega - (yp*cos_i*sin_omega)) * omega_dot)

    sat_posvel.loc[:,'vz'] = dyp*sin_i + yp*cos_i*di
    return sat_posvel


def correct_pseudorange(gpstime, gpsweek, ephem, pr_meas, rx_ecef=[[None]]):
    """Incorporate corrections in measurements.

    Incorporate clock corrections (relativistic, drift), tropospheric and
    ionospheric clock corrections.

    Parameters
    ----------
    gpstime : float
        Time of clock in seconds of the week
    gpsweek : int
        GPS week for time of clock
    ephem : pd.DataFrame
        Satellite ephemeris parameters for measurement SVs
    pr_meas : ndarray
        Ranging measurements from satellites [m]
    rx_ecef : ndarray
        1x3 array of ECEF rx_pos position [m]

    Returns
    -------
    pr_corr : ndarray
        Array of corrected pseudorange measurements [m]

    Notes
    -----
    Based on code written by J. Makela.
    AE 456, Global Navigation Sat Systems, University of Illinois
    Urbana-Champaign. Fall 2017

    """
    # TODO: Incorporate satellite clock rate changes into doppler measurements
    # TODO: Change default of rx_ecef to an array of None with size
    # TODO: Change the sign for corrections to what will be added to expected
    # measurements
    # TODO: Return corrections instead of corrected measurements

    # Load GPS Constants
    gpsconsts = GPSConsts()

    # Extract parameters
    # M_0  = ephem['M_0']
    # dN   = ephem['deltaN']

    e        = ephem['e']     # eccentricity
    sqrt_sma = ephem['sqrtA'] # sqrt of semi-major axis

    sqrt_mu_A = np.sqrt(gpsconsts.MUEARTH) * sqrt_sma**-3 # mean angular motion

    # Make sure gpstime and gpsweek are arrays
    if not isinstance(gpstime, np.ndarray):
        gpstime = np.array(gpstime)
    if not isinstance(gpsweek, np.ndarray):
        gpsweek = np.array(gpsweek)

    # Initialize the correction array
    pr_corr = pr_meas

    dt = gpstime - ephem['t_oe']
    if np.abs(dt).any() > 302400:
        dt = dt - np.sign(dt)*604800

    # Calculate the mean anomaly with corrections
    M_corr = ephem['deltaN'] * dt
    M      = ephem['M_0'] + (sqrt_mu_A * dt) + M_corr

    # Compute Eccentric Anomaly
    E = _compute_eccentric_anomoly(M, e, tol=1e-5)

    # Determine pseudorange corrections due to satellite clock corrections.
    # Calculate time offset from satellite reference time
    t_offset = gpstime - ephem['t_oc']
    if np.abs(t_offset).any() > 302400:
        t_offset = t_offset-np.sign(t_offset)*604800

    # Calculate clock corrections from the polynomial
    # corr_polynomial = ephem.af0
    #                 + ephem.af1*t_offset
    #                 + ephem.af2*t_offset**2
    corr_polynomial = (ephem['SVclockBias']
                     + ephem['SVclockDrift']*t_offset
                     + ephem['SVclockDriftRate']*t_offset**2)

    # Calcualte the relativistic clock correction
    corr_relativistic = gpsconsts.F * e * sqrt_sma * np.sin(E)

    # Calculate the total clock correction including the Tgd term
    clk_corr = (corr_polynomial - ephem['TGD'] + corr_relativistic)

    # NOTE: Removed ionospheric delay calculation here

    # calculate clock psuedorange correction
    pr_corr +=  clk_corr*gpsconsts.C

    if rx_ecef[0][0] is not None: # TODO: Reference using 2D array slicing
        # Calculate the tropospheric delays
        tropo_delay = calculate_tropo_delay(gpstime, gpsweek, ephem, rx_ecef)
        # Calculate total pseudorange correction
        pr_corr -= tropo_delay*gpsconsts.C

    if isinstance(pr_corr, pd.Series):
        pr_corr = pr_corr.to_numpy(dtype=float)

    # fill nans (fix for non-GPS satellites)
    pr_corr = np.where(np.isnan(pr_corr), pr_meas, pr_corr)

    return pr_corr


def calculate_tropo_delay(gpstime, gpsweek, ephem, rx_ecef):
    """Calculate tropospheric delay

    Parameters
    ----------
    gpstime : float
        Time of clock in seconds of the week
    gpsweek : int
        GPS week for time of clock
    ephem : pd.DataFrame
        Satellite ephemeris parameters for measurement SVs
    rx_ecef : ndarray
        1x3 array of ECEF rx_pos position [m]

    Returns
    -------
    tropo_delay : ndarray
        Tropospheric corrections to pseudorange measurements

    Notes
    -----
    Based on code written by J. Makela.
    AE 456, Global Navigation Sat Systems, University of Illinois
    Urbana-Champaign. Fall 2017

    """
    # Load gpsconstants
    gpsconsts = GPSConsts()

    # Make sure things are arrays
    if not isinstance(gpstime, np.ndarray):
        gpstime = np.array(gpstime)
    if not isinstance(gpsweek, np.ndarray):
        gpsweek = np.array(gpsweek)

    # Determine the satellite locations
    sat_posvel = find_sat(ephem, gpstime, gpsweek)
    _, sat_pos, _ = _extract_pos_vel_arr(sat_posvel)

    # compute elevation and azimuth
    el_az = find_elaz(rx_ecef, sat_pos)
    el_r  = np.deg2rad(el_az[:,0])

    # Calculate the WGS-84 latitude/longitude of the receiver
    rx_lla = ecef2geodetic(rx_ecef)
    height = rx_lla[:,2]

    # Force height to be positive
    ind = np.argwhere(height < 0).flatten()
    if len(ind) > 0:
        height[ind] = 0

    # Calculate the delay
    # TODO: Store these numbers somewhere, we should know where they're from -BC
    c_1 = 2.47
    c_2 = 0.0121
    c_3 = 1.33e-4
    tropo_delay = c_1/(np.sin(el_r)+c_2) * np.exp(-height*c_3)/gpsconsts.C

    return tropo_delay


def find_elaz(rx_pos, sat_pos):
    """Calculate the elevation and azimuth from a single receiver to multiple
    satellites.

    Parameters
    ----------
    rx_pos : ndarray
        1x3 vector containing [X, Y, Z] coordinate of receiver
    sat_pos : ndarray
        Nx3 array  containing [X, Y, Z] coordinates of satellites

    Returns
    -------
    el_az : ndarray
        Nx2 array containing the elevation and azimuth from the
        receiver to the requested satellites. Elevation and azimuth are
        given in decimal degrees.

    Notes
    -----
    Code written by J. Makela.
    AE 456, Global Navigation Sat Systems, University of Illinois
    Urbana-Champaign. Fall 2017

    """

    # check for 1D case:
    dim = len(rx_pos.shape)
    if dim == 1:
        rx_pos = np.reshape(rx_pos,(1,3))

    dim = len(sat_pos.shape)
    if dim == 1:
        sat_pos = np.reshape(sat_pos,(1,3))

    # Convert the receiver location to WGS84
    rx_lla = ecef2geodetic(rx_pos)
    assert np.shape(rx_lla)==(1,3)

    # Create variables with the latitude and longitude in radians
    lat = np.deg2rad(rx_lla[0,0])
    lon = np.deg2rad(rx_lla[0,1])

    # Create the 3 x 3 transform matrix from ECEF to ecef_to_ven
    cos_lon = np.cos(lon)
    cos_lat = np.cos(lat)
    sin_lon = np.sin(lon)
    sin_lat = np.sin(lat)
    ecef_to_ven = np.array([[ cos_lat*cos_lon,  cos_lat*sin_lon, sin_lat],
                            [-sin_lon        ,  cos_lon        , 0.     ],
                            [-sin_lat*cos_lon, -sin_lat*sin_lon, cos_lat]])

    # Replicate the rx_pos array to be the same size as the satellite array
    rx_array = np.ones_like(sat_pos) * rx_pos

    # Calculate the pseudorange for each satellite
    p = sat_pos - rx_array

    # Calculate the length of this vector
    n = np.array([np.sqrt(p[:,0]**2 + p[:,1]**2 + p[:,2]**2)])

    # Create the normalized unit vector
    p = p / (np.ones_like(p) * n.T)

    # Perform the transform of the normalized psueodrange from ECEF to VEN
    p_ven = np.dot(ecef_to_ven, p.T)

    # Calculate elevation and azimuth in degrees
    el_az = np.zeros([sat_pos.shape[0],2])
    el_az[:,0] = np.rad2deg((np.pi/2. - np.arccos(p_ven[0,:])))
    el_az[:,1] = np.rad2deg(np.arctan2(p_ven[1,:],p_ven[2,:]))

    return el_az

def _compute_eccentric_anomoly(M, e, tol=1e-5, max_iter=10):
    """Compute the eccentric anomaly from mean anomaly using the Newton-Raphson
    method using equation: f(E) = M - E + e * sin(E) = 0.

    Parameters
    ----------
    M : pd.DataFrame
        Mean Anomaly of GNSS satellite orbits
    e : pd.DataFrame
        Eccentricity of GNSS satellite orbits
    tol : float
        Tolerance for Newton-Raphson convergence
    max_iter : int
        Maximum number of iterations for Newton-Raphson

    Returns
    -------
    E : pd.DataFrame
        Eccentric Anomaly of GNSS satellite orbits

    """
    E = M
    for _ in np.arange(0, max_iter):
        f    = M - E + e * np.sin(E)
        dfdE = e*np.cos(E) - 1.
        dE   = -f / dfdE
        E    = E + dE

    if any(dE.iloc[:] > tol):
        print("Eccentric Anomaly may not have converged: dE = ", dE)

    return E
