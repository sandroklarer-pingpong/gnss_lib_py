"""Base class for moving values between different modules/functions

"""

__authors__ = "Ashwin Kanhere, D. Knowles"
__date__ = "03 Nov 2021"

import os
import copy

import numpy as np
import pandas as pd


class NavData():
    """gnss_lib_py specific class for handling data.
    Uses numpy for speed combined with pandas like intuitive indexing

    Attributes
    ----------
    arr_dtype : numpy.dtype
        Type of values stored in data array
    array : np.ndarray
        Array containing data, dimension M x N
    map : Dict
        Map of the form {pandas column name : array row number }
    str_map : Dict
        Map of the form {pandas column name : {array value : string}}.
        Map is of the form {pandas column name : {}} for non string rows.
    """
    def __init__(self, csv_path=None, pandas_df=None, numpy_array=None,
                 **kwargs):
        # For a Pythonic implementation,
        # including all attributes as None in the beginning
        self.arr_dtype = np.float32 # default value
        self.array = None
        self.map = {}
        self.str_map = {}

        # Attributes for looping over all columns

        self.curr_col = 0
        self.num_cols = 0

        if csv_path is not None:
            self.from_csv_path(csv_path, **kwargs)
        elif pandas_df is not None:
            self.from_pandas_df(pandas_df)
        elif numpy_array is not None:
            self.from_numpy_array(numpy_array)
        else:
            self.build_navdata()

        self.rename(rows=self._row_map(), inplace=True)

        self.postprocess()

    def postprocess(self):
        """Postprocess loaded data. Optional in subclass
        """

    def build_navdata(self):
        """Build attributes for NavData.

        """
        self.array = np.zeros((0,0), dtype=self.arr_dtype)

    def from_csv_path(self, csv_path, **kwargs):
        """Build attributes of NavData using csv file.

        Parameters
        ----------
        csv_path : string
            Path to csv file containing data
        header : string, int, or None
            "infer" uses the first row as column names, setting to
            None will add int names for the columns.
        sep : char
            Delimiter to use when reading in csv file.

        """
        if not isinstance(csv_path, str):
            raise TypeError("csv_path must be string")
        if not os.path.exists(csv_path):
            raise OSError("file not found")

        self.build_navdata()

        pandas_df = pd.read_csv(csv_path, **kwargs)
        self.from_pandas_df(pandas_df)

    def from_pandas_df(self, pandas_df):
        """Build attributes of NavData using pd.DataFrame.

        Parameters
        ----------
        pandas_df : pd.DataFrame of data
        """

        if not isinstance(pandas_df, pd.DataFrame):
            raise TypeError("pandas_df must be pd.DataFrame")

        if pandas_df.columns.dtype != object:
            # default headers are Int64 type, but for the NavData
            # class they need to be strings
            pandas_df.rename(str, axis="columns", inplace=True)

        self.build_navdata()

        for _, col_name in enumerate(pandas_df.columns):
            new_value = pandas_df[col_name].to_numpy()
            self[col_name] = new_value

    def from_numpy_array(self, numpy_array):
        """Build attributes of NavData using np.ndarray.

        Parameters
        ----------
        numpy_array : np.ndarray
            Numpy array containing data

        """

        if not isinstance(numpy_array, np.ndarray):
            raise TypeError("numpy_array must be np.ndarray")

        self.build_navdata()

        for row_num in range(numpy_array.shape[0]):
            self[str(row_num)] = numpy_array[row_num,:]

    @staticmethod
    def _row_map():
        """Map of column names from loaded to gnss_lib_py standard

        Initializes as an emptry dictionary, must be reimplemented for
        custom parsers.

        Returns
        -------
        row_map : Dict
            Dictionary of the form {old_name : new_name}
        """

        row_map = {}

        return row_map

    def pandas_df(self):
        """Return pandas DataFrame equivalent to class

        Returns
        -------
        df : pd.DataFrame
            DataFrame with data, including strings as strings
        """

        df_list = []
        for row in self.rows:
            df_list.append(np.atleast_1d(self[row]))

        # transpose list to conform to Pandas input
        df_list = [list(x) for x in zip(*df_list)]

        dframe = pd.DataFrame(df_list,columns=self.rows)

        return dframe

    def get_strings(self, key):
        """Return list of strings for given key

        Parameters
        ----------
        key : string for column name required as string

        Returns
        -------
        values_str : np.ndarray
            1D array with string entries corresponding to dataset
        """
        values_int = self.array[self.map[key],:].astype(int)
        values_str = values_int.astype(object, copy=True)
        # True by default but making explicit for clarity
        for str_key, str_val in self.str_map[key].items():
            values_str[values_int==str_key] = str_val
        return values_str

    def save_csv(self, output_path="navdata.csv"): #pragma: no cover
        """Save data as csv

        Parameters
        ----------
        output_path : string
            Path where csv should be saved
        """
        pd_df = self.pandas_df()
        pd_df.to_csv(output_path)

    def _parse_key_idx(self, key_idx):
        """Break down input queries to relevant row and column indices

        Parameters
        ----------
        key_idx : str/list/tuple/slice/int
            Query for array items

        Returns
        -------
        rows : slice/list
            Rows to extract from the array
        cols : slice/list
            Columns to extract from the array
        """
        if isinstance(key_idx, str):
            rows = [self.map[key_idx]]
            cols = slice(None, None)
        elif isinstance(key_idx, list) and isinstance(key_idx[0], str):
            rows = [self.map[k] for k in key_idx]
            cols = slice(None, None)
        elif isinstance(key_idx, slice):
            rows = key_idx
            cols = slice(None, None)
        elif isinstance(key_idx, int):
            rows = [key_idx]
            cols = slice(None, None)
        else:
            if isinstance(key_idx[1], int):
                cols = [key_idx[1]]
            else:
                cols = key_idx[1]

            rows = []
            if isinstance(key_idx[0], slice):
                rows = key_idx[0]
            elif isinstance(key_idx[0], int):
                rows = [key_idx[0]]
            else:
                if not isinstance(key_idx[0],list):
                    row_key = [key_idx[0]]
                else:
                    row_key = key_idx[0]
                for key in row_key:
                    rows.append(self.map[key])
        return rows, cols

    def _get_str_rows(self, rows):
        """Checks which input rows contain string elements

        Parameters
        ----------
        rows : slice/list
            Rows to check for string elements

        Returns
        -------
        row_list : list
            Input rows, strictly in list format
        row_str : list
            List of boolean values indicating which rows contain strings
        """
        _row_idx_str_bool = self._row_idx_str_bool
        if isinstance(rows, slice):
            slice_idx = rows.indices(self.shape[0])
            row_list = np.arange(slice_idx[0], slice_idx[1], slice_idx[2])
            row_str = [_row_idx_str_bool[row] for row in row_list]
        else:
            row_list = list(rows)
            row_str = [_row_idx_str_bool[row] for row in rows]
        return row_list, row_str

    def _get_set_str_rows(self, rows, new_value):
        """Checks which output rows contain string elements given input.

        If the row used to be string values but now is numeric, this
        function also empties the corresponding dictionary in str_map.

        Parameters
        ----------
        rows : slice/list
            Rows to check for string elements
        new_value : np.ndarray/list/int
            Values to be added to self.array attribute

        Returns
        -------
        row_list : list
            Input rows, strictly in list format
        row_str_new : list
            List of boolean values indicating which of the new rows
            contain strings.
        """

        row_list, row_str_existing = self._get_str_rows(rows)

        if isinstance(new_value, np.ndarray) and (new_value.dtype in (object,str) \
                        or np.issubdtype(new_value.dtype,np.dtype('U'))):
            if type(new_value.item(0)) in (int, float):
                row_str_new = [False]*len(row_list)
            else:
                row_str_new = [True]*len(row_list)
        elif isinstance(np.asarray(new_value).item(0), str):
            raise RuntimeError("Cannot set a row with list of strings, \
                             please use np.ndarray with dtype=object")
        else:
            row_str_new = [False]*len(row_list)

        for row_idx, row in enumerate(row_list):
            if row_str_existing[row_idx] and not row_str_new[row_idx]:
                # changed from string to numeric
                self.str_map[self.inv_map[row]] = {}


        return row_list, row_str_new

    def __getitem__(self, key_idx):
        """Return item indexed from class

        Parameters
        ----------
        key_idx : str/list/tuple/slice/int
            Query for array items

        Returns
        -------
        arr_slice : np.ndarray
            Array of data containing row names and time indexed
            columns. The return is squeezed meaning that all dimensions
            of the output that are length of one are removed
        """
        rows, cols = self._parse_key_idx(key_idx)
        row_list, row_str = self._get_str_rows(rows)
        assert np.all(row_str) or np.all(np.logical_not(row_str)), \
                "Cannot assign/return combination of strings and numbers"
        if np.all(row_str):
            # Return sliced strings
            # arr_slice  = []
            arr_slice = np.atleast_2d(np.empty_like(self.array[rows, cols], dtype=object))
            for row_num, row in enumerate(row_list):
                str_arr = self.get_strings(self.inv_map[row])
                arr_slice[row_num, :] = str_arr[cols]
                # arr_slice.append(str_arr[ cols])
        else:
            arr_slice = self.array[rows, cols]

        # remove all dimensions of length one
        arr_slice = np.squeeze(arr_slice)

        return arr_slice

    def __setitem__(self, key_idx, new_value):
        """Add/update rows.

        __setitem__ expects that the shape of the value being passed
        matches the shape of the internal arrays that have to be set.
        So, if 2 variable types (rows) at 10 instances (columns) need to
        be set, the input new_value must be of shape (2, 10).
        If the shape is (10, 2) the assignment operator will raise a
        ValueError. This applies to all types of value assignments.

        Parameters
        ----------
        key_idx : str/list/tuple/slice/int
            Query for array items to set
        new_value : np.ndarray/list/int
            Values to be added to self.array attribute

        """
        if isinstance(key_idx, int) and len(self.map)<=key_idx:
            raise KeyError('Row indices must be strings when assigning new values')
        if isinstance(key_idx, slice) and len(self.map)==0:
            raise KeyError('Row indices must be strings when assigning new values')
        if isinstance(key_idx, str) and key_idx not in self.map:
            #Creating an entire new row
            if isinstance(new_value, np.ndarray) \
                    and (new_value.dtype in (object,str) \
                    or np.issubdtype(new_value.dtype,np.dtype('U'))):                # Adding string values
                # string values
                new_value = new_value.astype(str)
                new_str_vals = len(np.unique(new_value))*np.ones(np.shape(new_value),
                                    dtype=self.arr_dtype)
                new_str_vals = self._str_2_val(new_str_vals, new_value, key_idx)
                if self.array.shape == (0,0):
                    # if empty array, start from scratch
                    self.array = np.reshape(new_str_vals, [1, -1])
                else:
                    # if array is not empty, add to it
                    self.array = np.vstack((self.array, np.reshape(new_str_vals, [1, -1])))
                self.map[key_idx] = self.shape[0]-1
            else:
                # numeric values
                if not isinstance(new_value, int) \
                and not isinstance(new_value, float) \
                and new_value.size > 0:
                    assert not isinstance(np.asarray(new_value).item(0), str), \
                            "Cannot set a row with list of strings, \
                            please use np.ndarray with dtype=object"
                # Adding numeric values
                self.str_map[key_idx] = {}
                if self.array.shape == (0,0):
                    # if empty array, start from scratch
                    self.array = np.reshape(new_value, (1,-1))
                    # have to explicitly convert to float in case
                    # numbers were interpretted as integers
                    self.array = self.array.astype(self.arr_dtype)
                else:
                    # if array is not empty, add to it
                    self.array = np.vstack((self.array, np.empty([1, len(self)])))
                    self.array[-1, :] = np.reshape(new_value, -1)
                self.map[key_idx] = self.shape[0]-1
        else:
            # Updating existing rows or columns
            rows, cols = self._parse_key_idx(key_idx)
            row_list, row_str = self._get_set_str_rows(rows,new_value)
            assert np.all(row_str) or np.all(np.logical_not(row_str)), \
                "Cannot assign/return combination of strings and numbers"
            if np.all(row_str):
                assert isinstance(new_value, np.ndarray) \
                   and (new_value.dtype in (object,str) \
                     or np.issubdtype(new_value.dtype,np.dtype('U'))), \
                        "String assignment only supported for ndarray of type object"
                inv_map = self.inv_map
                new_value = np.atleast_2d(new_value)
                # new_value = np.reshape(new_value, [-1, new_value.shape[0]])
                new_str_vals = np.ones_like(new_value, dtype=self.arr_dtype)
                for row_num, row in enumerate(row_list):
                    key = inv_map[row]
                    new_value_row = new_value[row_num , :]
                    new_str_vals_row = new_str_vals[row_num, :]
                    new_str_vals[row_num, :] = self._str_2_val(new_str_vals_row,
                                                    new_value_row, key)
                self.array[rows, cols] = new_str_vals
            else:
                if not isinstance(new_value, int):
                    assert not isinstance(np.asarray(new_value)[0], str), \
                            "Please use dtype=object for string assignments"
                self.array[rows, cols] = new_value

    def _str_2_val(self, new_str_vals, new_value, key):
        """Convert string valued arrays to values for storing in array

        Parameters
        ----------
        new_str_vals : np.ndarray
            Array of dtype=self.arr_dtype where numeric values are to be
            stored
        new_value : np.ndarray
            Array of dtype=object, containing string values that are to
            be converted
        key : string
            Key indicating row where string to numeric conversion is
            required
        """
        if key in self.map:
            # Key already exists, update existing string value dictionary
            inv_str_map = {v: k for k, v in self.str_map[key].items()}
            string_vals = np.unique(new_value)
            str_map_dict = self.str_map[key]
            total_str = len(self.str_map[key])
            for str_val in string_vals:
                if str_val not in inv_str_map.keys():
                    str_map_dict[total_str] = str_val
                    new_str_vals[new_value==str_val] = total_str
                    total_str += 1
                else:
                    new_str_vals[new_value==str_val] = inv_str_map[str_val]
            self.str_map[key] = str_map_dict
        else:
            string_vals = np.unique(new_value)
            str_dict = dict(enumerate(string_vals))
            self.str_map[key] = str_dict
            new_str_vals = len(string_vals)*np.ones(np.shape(new_value),
                                                   dtype=self.arr_dtype)
            # Set unassigned value to int not accessed by string map
            for str_key, str_val in str_dict.items():
                if new_str_vals.size == 1:
                    new_str_vals = np.array(str_key,dtype=self.arr_dtype)
                else:
                    new_str_vals[new_value==str_val] = str_key
            # Copy set to false to prevent memory overflows
            new_str_vals = np.round(new_str_vals.astype(self.arr_dtype,
                                                        copy=False))
        return new_str_vals

    def add(self, csv_path=None, pandas_df=None, numpy_array=None):
        """Add new timesteps to existing array

        Parameters
        ----------
        csv_path : string
            Path to csv file containing data to add
        pandas_df : pd.DataFrame
            DataFrame containing data to add
        numpy_array : np.ndarray
            Array containing only numeric data to add
        """
        old_row_num = len(self.map)
        old_len = len(self)
        new_data_cols = slice(old_len, None)
        if numpy_array is not None:
            if old_row_num == 0:
                self.from_numpy_array(numpy_array)
            else:
                if len(numpy_array.shape)==1:
                    numpy_array = np.reshape(numpy_array, [1, -1])
                self.array = np.hstack((self.array, np.empty_like(numpy_array,
                                        dtype=self.arr_dtype)))
                self[:, new_data_cols] = numpy_array
        if csv_path is not None:
            if old_row_num == 0:
                self.from_csv_path(csv_path)
            else:
                pandas_df = pd.read_csv(csv_path)
        if pandas_df is not None:
            if old_row_num == 0:
                self.from_pandas_df(pandas_df)
            else:
                self.array = np.hstack((self.array, np.empty(pandas_df.shape).T))
                for col in pandas_df.columns:
                    self[col, new_data_cols] = np.asarray(pandas_df[col].values)

    def where(self, key_idx, value, condition="eq"):
        """Return NavData where conditions are met for the given row

        Parameters
        ----------
        key_idx : string/int
            Key or index of the row in which conditions will be checked
        value : float/list
            Number (or list of two numbers for ) to compare array values
            against
        condition : string
            Condition type (greater than ("greater")/ less than ("lesser")/
            equal to ("eq")/ greater than or equal to ("geq")/
            lesser than or equal to ("leq") / in between ("between")
            inclusive of the provided limits

        Returns
        -------
        new_navdata : gnss_lib_py.parsers.navdata.NavData
            NavData with columns where given condition is satisfied
            for specified row
        """
        # Add a condition here instead of just comparing to a value.
        # Do so by adding a parameter for less than inequality, equality and
        # greater than inequality
        new_cols = self.argwhere(key_idx, value, condition)
        new_navdata = self.copy(cols=new_cols)
        return new_navdata

    def argwhere(self, key_idx, value, condition):
        """Return columns where conditions are met for the given row

        Parameters
        ----------
        key_idx : string/int
            Key or index of the row in which conditions will be checked
        value : float/list
            Number (or list of two numbers for ) to compare array values against
        condition : string
            Condition type (greater than ("greater")/ less than ("lesser")/
            equal to ("eq")/ greater than or equal to ("geq")/
            lesser than or equal to ("leq") / in between ("between"))

        Returns
        -------
        new_cols : list
            Columns in NavData where given condition is satisfied
            for specified row
        """
        rows, _ = self._parse_key_idx(key_idx)
        inv_map = self.inv_map
        row_list, row_str = self._get_str_rows(rows)
        if len(row_list)>1:
            error_msg = "where does not currently support multiple rows"
            raise NotImplementedError(error_msg)
        row = row_list[0]
        row_str = row_str[0]
        if row_str:
            # Values in row are strings
            if condition != "eq":
                raise ValueError("Inequality comparison not valid for strings")
            key = inv_map[row]
            for str_key, str_value in self.str_map[key].items():
                if str_value==value:
                    new_cols = np.argwhere(self.array[row, :]==str_key)
                    break
            # Extract columns where condition holds true and return new NavData
        else:
            # Values in row are numerical
            # Find columns where value can be found and return new NavData
            if condition=="eq":
                new_cols = np.argwhere(self.array[row, :]==value)
            elif condition == "leq":
                new_cols = np.argwhere(self.array[row, :]<=value)
            elif condition == "geq":
                new_cols = np.argwhere(self.array[row, :]>=value)
            elif condition == "greater":
                new_cols = np.argwhere(self.array[row, :]>value)
            elif condition == "lesser":
                new_cols = np.argwhere(self.array[row, :]<value)
            elif condition == "between":
                assert len(value)==2, "Please give both lower and upper bound for between"
                new_cols = np.argwhere(np.logical_and(self.array[row, :]>=value[0],
                                        self.array[row, :]<= value[1]))
            else:
                raise ValueError("Condition not implemented")
        new_cols = np.squeeze(new_cols)
        return new_cols

    def loop_time(self, time_row, tol_decimals=2):
        """Generator object to loop over columns from same times

        Parameters
        ----------
        time_row : string/int
            Key or index of the row in which times are stored
        tol_decimals : int
            Decimal places after which times are considered equal

        Yields
        ------
        delta_t : float
            Difference between current time and previous time
        new_navdata : gnss_lib_py.parsers.navdata.NavData
            NavData with same time, upto given decimal tolerance
        """
        times = self[time_row]
        times_unique = np.sort(np.unique(np.around(times,
                                         decimals=tol_decimals)))
        for time_idx, time in enumerate(times_unique):
            if time_idx==0:
                delta_t = 0
            else:
                delta_t = times_unique[time_idx]-times_unique[time_idx-1]
            new_navdata = self.where(time_row, [time-10**(-tol_decimals),
                                                time+10**(-tol_decimals)],
                                                condition="between")
            yield delta_t, new_navdata

    def __iter__(self):
        """Initialize iterator over NavData (iterates over all columns)

        Returns
        -------
        self: gnss_lib_py.parsers.NavData
            Instantiation of NavData class with iteration initialized
        """
        self.curr_col = 0
        self.num_cols = np.shape(self.array)[1]
        return self

    def __next__(self):
        """Method to get next item when iterating over NavData class

        Returns
        -------
        x_curr : gnss_lib_py.parsers.NavData
            Current column (based on iteration count)
        """
        if self.curr_col >= self.num_cols:
            raise StopIteration
        x_curr = self.copy(rows=None, cols=self.curr_col)
        self.curr_col += 1
        return x_curr

    def __str__(self):
        str_out = str(self.pandas_df())
        str_out = str_out.replace("DataFrame","NavData")
        str_out = str_out.replace("Columns","Rows")
        return str_out

    def __len__(self):
        """Return length of class

        Returns
        -------
        length : int
            Number of time steps in NavData
        """
        length = np.shape(self.array)[1]
        return length

    @property
    def shape(self):
        """Return shape of class

        Returns
        -------
        shp : tuple
            (M, N), M is number of rows and N number of time steps
        """
        shp = np.shape(self.array)
        return shp

    @property
    def rows(self):
        """Return all row names in instance as a list

        Returns
        -------
        rows : list
            List of row names in NavData
        """
        rows = list(self.map.keys())
        return rows


    @property
    def _row_idx_str_bool(self):
        """Dictionary of index : if data entry is string.

        Row has string values if the string map is nonempty for a
        given row.

        Returns
        -------
        _row_idx_str_bool : Dict
            Dictionary of whether data at row number key is string or not
        """
        _row_idx_str_bool = {self.map[k]: bool(len(self.str_map[k])) for k in self.str_map}
        return _row_idx_str_bool

    def is_str(self, row_name):
        """Check whether a row contained string values.

        Parameters
        ----------
        row_name : string
            Name of the row to check whether it contains string values.

        Returns
        -------
        contains_str : bool
            True if the row contains string values, False otherwise.

        """

        if row_name not in self.map:
            raise KeyError("'" + str(row_name) \
                           + "' key doesn't exist in NavData class")

        contains_str = self._row_idx_str_bool[self.map[row_name]]

        return contains_str

    @property
    def inv_map(self):
        """Inverse dictionary map for label and row_number map

        Returns
        -------
        inv_map: Dict
            Dictionary of row_number : label
        """
        inv_map = {v: k for k, v in self.map.items()}
        return inv_map

    def rename(self, mapper=None, rows=None, inplace=False):
        """Rename data within rows or row names of NavData class.

        Row names must be strings.

        Parameters
        ----------
        mapper : dict
            Pairs of {"old_name" : "new_name"} for each value to
            replace. Values are replaced for each row in "rows" if
            "rows" is specified or for all rows if "rows" is left
            defaulted to None.
        rows : dict, or array-like
            If a dictionary is passed, then rows will be renamed
            according to pairs of {"old_name" : "new_name"}.
            If mapper is not None, then an array-like input may be
            passed to indicate which rows of values should be remapped.

        inplace : bool
            If False, will return new NavData instance with data and/or
            rows renamed. If True, will rename data and/or rows in the
            current NavData instance.

        Returns
        -------
        new_navdata : gnss_lib_py.parsers.navdata.NavData or None
            If inplace is False, returns NavData instance after renaming
            specified data and/or rows. If inplace is True, returns
            None.

        """

        if not (isinstance(mapper, dict) or mapper is None):
            raise TypeError("'mapper' must be dict or None")
        if isinstance(rows,str):
            rows = [rows]
        if not (type(rows) in (dict, list, np.ndarray, tuple, set) \
           or rows is None):
            raise TypeError("'rows' must be dict, array-like or None")
        if not isinstance(inplace, bool):
            raise TypeError("'inplace' must be bool")
        if rows is not None:
            for old_name in rows:
                if old_name not in self.map:
                    raise KeyError("'" + str(old_name) + "' row name " \
                                 + "doesn't exist in NavData class")
        if rows is not None:
            # convert to None if rows is emptry
            rows = None if len(rows)==0 else rows
        if not inplace:
            new_navdata = self.copy()   # create copy to return
        if mapper is not None:
            remap_rows = self.rows if rows is None else rows
            for row in remap_rows:
                new_row_values = list(self[row])
                for old_value, new_value in mapper.items():
                    new_row_values = [new_value if v == old_value else v for v in new_row_values]
                if inplace:
                    self[row] = np.array(new_row_values)
                else:
                    new_navdata[row] = np.array(new_row_values)
        if isinstance(rows,dict):
            for old_name, new_name in rows.items():
                if not isinstance(new_name, str):
                    raise TypeError("Row names must be strings")
                if inplace:
                    self.map[new_name] = self.map.pop(old_name)
                    self.str_map[new_name] = self.str_map.pop(old_name)
                else:
                    new_navdata.map[new_name] = new_navdata.map.pop(old_name)
                    new_navdata.str_map[new_name] = new_navdata.str_map.pop(old_name)

        if inplace:
            return None
        return new_navdata

    def copy(self, rows=None, cols=None):
        """Return copy of NavData keeping specified rows and columns

        If None is passed into either argument, all rows or cols
        respectively are returned.

        If no arguments are added .copy() returns a full copy of the
        entire NavData class.

        Parameters
        ----------
        rows : None/list/np.ndarray
            Strings or integers indicating rows to keep in copy.
            Defaults to None meaning all rows are copied.
        cols : None/list/np.ndarray
            Integers indicating columns to keep in copy. Defaults to
            None meaning all cols are copied.

        Returns
        -------
        new_navdata : gnss_lib_py.parsers.navdata.NavData
            Copy of original NavData with desired rows and columns
        """
        new_navdata = NavData()
        inv_map = self.inv_map
        if rows is None:
            # row_indices = slice(None, None).indices(len(self.rows))
            # rows = np.arange(row_indices[0], row_indices[1], row_indices[2])
            rows = self.rows
        if cols is None:
            col_indices = slice(None, None).indices(len(self))
            cols = np.arange(col_indices[0], col_indices[1], col_indices[2])
        for row_idx in rows:
            new_row = copy.deepcopy(self[row_idx, cols])
            if isinstance(row_idx, int):
                key = inv_map[row_idx]
            else:
                key = row_idx
            new_navdata[key] = new_row
        return new_navdata

    def remove(self, rows=None, cols=None, inplace=False):
        """Reset NavData to remove specified rows and columns

        Parameters
        ----------
        rows : None/list/np.ndarray/tuple
            Rows to remove from NavData
        cols : None/list/np.ndarray/tuple
            Columns to remove from NavData
        inplace : bool
            If False, will return new NavData instance with specified
            rows and columns removed. If True, will remove rows and
            columns from the current NavData instance.

        Returns
        -------
        new_navdata : gnss_lib_py.parsers.navdata.NavData or None
            If inplace is False, returns NavData instance after removing
            specified rows and columns. If inplace is True, returns
            None.

        """
        if cols is None:
            cols = []
        if rows is None:
            rows = []
        if isinstance(rows,str):
            rows = [rows]
        new_navdata = NavData()
        if len(rows) != 0 and isinstance(rows[0], int):
            try:
                rows = [self.inv_map[row_idx] for row_idx in rows]
            except KeyError as exception:
                raise KeyError("row '" + str(exception) + "' is out " \
                             + "of bounds of data.") from Exception

        for row in rows:
            if row not in self.rows:
                raise KeyError("row '" + row + "' does not exist so " \
                             + "cannont be removed.")
        for col in cols:
            if col >= len(self):
                raise KeyError("column '" + str(col) + "' exceeds " \
                             + "NavData dimensions, so cannont be " \
                             + "removed.")

        if inplace: # remove rows/cols from current column

            # delete rows and columns from self.array
            del_row_idxs = [self.map[row] for row in rows]
            self.array = np.delete(self.array,del_row_idxs,axis=0)
            self.array = np.delete(self.array,cols,axis=1)

            # delete keys from self.map and self.str_map
            for row in rows:
                del self.map[row]
                del self.str_map[row]

            # reindex self.map
            self.map = {key : index for index, key in \
                enumerate(sorted(self.map, key=lambda k: self.map[k]))}

            return None

        # inplace = False; return new instance with rows/cols removed
        keep_rows = [row for row in self.rows if row not in rows]
        keep_cols = [col for col in range(len(self)) if col not in cols]
        for row_idx in keep_rows:
            new_row = self[row_idx, keep_cols]
            key = row_idx
            new_navdata[key] = new_row
        return new_navdata

    def in_rows(self, rows):
        """Checks whether the given rows are in NavData.

        If the rows are not in NavData, it creates a KeyError and lists
        all non-existent rows.

        Parameters
        ----------
        rows : string or list/np.ndarray/tuple of strings
            Indexes to check whether they are rows in NavData
        """

        if isinstance(rows,str):
            rows = [rows]
        if type(rows) in (list, np.ndarray, tuple):
            if isinstance(rows,np.ndarray):
                rows = np.atleast_1d(rows)
            missing_rows = ["'"+row+"'" for row in rows if row not in self.rows]
        else:
            raise KeyError("input to in_rows must be a single row " \
                         + "index or list/np.ndarray/tuple of indexes")

        if len(missing_rows) > 0:
            raise KeyError(", ".join(missing_rows) + " row(s) are" \
                           + " missing from NavData object.")
