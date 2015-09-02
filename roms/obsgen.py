#!/usr/bin/env python
"""
  genobs.py

  State Estimation and Analysis for PYthon

  Module to process observations:
    obsgen : class to convert from raw to ROMS observations using
             specific subclasses

  Written by Brian Powell on 08/15/15
  Copyright (c)2015 University of Hawaii under the BSD-License.
"""

from __future__ import print_function

import numpy as np
import netCDF4
import seapy
import datetime
from warnings import warn

class obsgen(object):
    def __init__(self, grid, dt, reftime=seapy.default_epoch):
        """
        class for abstracting the processing of raw observation files
        (satellite, in situ, etc.) into ROMS observations files. All
        processing has commonalities which this class encapsulates, while
        leaving the loading and translation of individual data formats
        to subclasses.

        Parameters
        ----------
        grid: seapy.model.grid or string,
            grid to use for generating observations
        dt: float,
            Model time-step or greater in units of days
        epoch: datetime, optional,
            Time to reference all observations from

        Returns
        -------
        None

        """
        self.grid = seapy.model.asgrid(grid)
        self.dt = dt
        self.epoch = reftime

    def convert_file(self, file, title=None):
        """
        convert a raw observation file into a ROMS observations structure.
        The subclasses are responsible for the conversion, and this method
        is obsgen is only a stub.

        Parameters
        ----------
        file : string,
            filename of the file to process
        title : string,
            Title to give the new observation structure global attribute

        Returns
        -------
        seapy.roms.obs.obs,
            observation structure from raw obs
        """
        pass

    def batch_files(self, in_files, out_files):
        """
        Given a list of input files, process each one and save each result
        into the given output file.

        Parameters
        ----------
        in_files : list of strings,
            filenames of the files to process
        out_files : list of strings,
            filenames of the files to create for each of the input filenames.
            If a single string is given, the character '#' will be replaced
            by the starting time of the observation (e.g. out_files="out_#.nc"
            will become out_03234.nc)

        Returns
        -------
        None
        """
        import re

        outtime = False
        if isinstance(out_files, str):
            outtime = True
            time = re.compile('\#')

        for n,file in enumerate(in_files):
            try:
                print(file)
                obs = self.convert_file(file)
                if obs is None:
                    continue
                if outtime:
                    obs.to_netcdf(time.sub("{:05d}".format(int(obs.time[0])),
                                           out_files))
                else:
                    obs.to_netcdf(out_files[n])
            except:
                warn("WARNING: "+file+" cannot be processed.")
        pass

class aquarius_sss(obsgen):
    """
    class to process Aquarius SSS HDF5 files into ROMS observation
    files. This is a subclass of seapy.roms.genobs.genobs, and handles
    the loading of the data.
    """
    def __init__(self, grid, dt, reftime=seapy.default_epoch, salt_limits=None,
                 salt_error=0.1):
        if salt_limits is None:
            self.salt_limits = (10, 36)
        else:
            self.salt_limits = salt_limits
        self.salt_error = salt_error
        super().__init__(grid, dt, epoch)

    def convert_file(self, file, title="AQUARIUS Obs"):
        """
        Load an Aquarius file and convert into an obs structure
        """
        import h5py

        f = h5py.File(file,'r')
        salt = np.ma.masked_equal(np.flipud(f['l3m_data'][:]),
                                  f['l3m_data'].attrs['_FillValue'])
        year = f.attrs['Period End Year']
        day = f.attrs['Period End Day']
        nlat = f.attrs['Northernmost Latitude']-0.5
        slat = f.attrs['Southernmost Latitude']+0.5
        wlon = f.attrs['Westernmost Longitude']+0.5
        elon = f.attrs['Easternmost Longitude']-0.5
        dlat = f.attrs['Latitude Step']
        dlon = f.attrs['Longitude Step']
        f.close()

        [lon, lat] = np.meshgrid(np.arange(wlon,elon+dlon,dlon),
                                 np.arange(slat,nlat+dlat,dlat))
        time = (datetime.datetime(year,1,1) + datetime.timedelta(int(day)) -
               self.epoch).days
        lat = lat.flatten()
        lon = lon.flatten()
        if self.grid.east():
            lon[lon<0] += 360

        salt = np.ma.masked_outside(salt.flatten(), self.salt_limits[0],
                                    self.salt_limits[1])
        data = [seapy.roms.obs.raw_data("SALT", "SSS_AQUARIUS",
                                        salt, None, self.salt_error)]
        # Grid it
        return seapy.roms.obs.gridder(self.grid, time, lon, lat, None,
                                      data, self.dt, title)
        pass

class argo_ctd(obsgen):
    """
    class to process ARGO CTD netcdf files into ROMS observation
    files. This is a subclass of seapy.roms.genobs.genobs, and handles
    the loading of the data.
    """
    def __init__(self, grid, dt, reftime=seapy.default_epoch, temp_limits=None,
                 salt_limits=None, temp_error=0.25,
                 salt_error=0.1):
        if temp_limits is None:
            self.temp_limits = (5, 30)
        else:
            self.temp_limits = temp_limits
        if salt_limits is None:
            self.salt_limits = (10, 35.5)
        else:
            self.salt_limits = salt_limits
        self.temp_error = temp_error
        self.salt_error = salt_error
        super().__init__(grid, dt, epoch)

    def convert_file(self, file, title="Argo Obs"):
        """
        Load an Argo file and convert into an obs structure
        """
        nc = netCDF4.Dataset(file)

        # Load the position of all profiles in the file
        lon = nc.variables["LONGITUDE"][:]
        lat = nc.variables["LATITUDE"][:]
        pro_q = nc.variables["POSITION_QC"][:].astype(int)
        # Find the profiles that are in our area with known locations quality
        if not self.grid.east():
            lon[lon>180] -= 360
        profile_list = np.where(np.logical_and.reduce((
                    lat >= np.min(self.grid.lat_rho),
                    lat <= np.max(self.grid.lat_rho),
                    lon >= np.min(self.grid.lon_rho),
                    lon <= np.max(self.grid.lon_rho),
                    pro_q == 1)))[0]

        # Check which are good profiles
        profile_qc = nc.variables["PROFILE_PRES_QC"][profile_list].astype('<U1')
        profile_list = profile_list[profile_qc == 'A']
        if not profile_list.size:
            return None

        # Load only the data from those in our area
        lon = lon[profile_list]
        lat = lat[profile_list]
        julian_day = nc.variables["JULD_LOCATION"][profile_list]
        argo_epoch = datetime.datetime.strptime(''.join( \
            nc.variables["REFERENCE_DATE_TIME"][:].astype('<U1')),'%Y%m%d%H%M%S')
        time_delta = (self.epoch - argo_epoch).days
        file_stamp = datetime.datetime.strptime(''.join( \
            nc.variables["DATE_CREATION"][:].astype('<U1')),'%Y%m%d%H%M%S')

        # Grab data over the previous day
        file_time = np.minimum((file_stamp - argo_epoch).days,
                               int(np.max(julian_day)))
        time_list = np.where(julian_day >= file_time - 1)
        profile_list = profile_list[time_list]
        julian_day = julian_day[time_list]
        lon = lon[time_list]
        lat = lat[time_list]

        # Load the data in our region and time
        temp = nc.variables["TEMP"][profile_list,:]
        temp_qc = nc.variables["TEMP_QC"][profile_list,:]
        salt = nc.variables["PSAL"][profile_list,:]
        salt_qc = nc.variables["PSAL_QC"][profile_list,:]
        pres = nc.variables["PRES"][profile_list,:]
        pres_qc = nc.variables["PRES_QC"][profile_list,:]
        nc.close()

        # Combine the QC codes
        qc = np.mean(np.vstack((temp_qc.compressed(), salt_qc.compressed(),
                                pres_qc.compressed())).astype(int), axis=0)
        good_data = np.where(qc == 1)

        # Put everything together into individual observations
        time = np.resize(julian_day-time_delta,
                         pres.shape[::-1]).T[~temp.mask][good_data]
        lat = np.resize(lat, pres.shape[::-1]).T[~temp.mask][good_data]
        lon = np.resize(lon, pres.shape[::-1]).T[~temp.mask][good_data]
        depth = -seapy.seawater.depth(pres.compressed()[good_data], lat)

        # Apply the limits
        temp = np.ma.masked_outside(temp.compressed()[good_data],
                                    self.temp_limits[0], self.temp_limits[1])
        salt = np.ma.masked_outside(salt.compressed()[good_data],
                                    self.salt_limits[0], self.salt_limits[1])

        data = [seapy.roms.obs.raw_data("TEMP", "CTD_ARGO", temp,
                                        None, self.temp_error),
                seapy.roms.obs.raw_data("SALT", "CTD_ARGO", salt,
                                        None, self.salt_error)]

        return seapy.roms.obs.gridder(self.grid, time, lon, lat, depth,
                                      data, self.dt, title)

class aviso_sla_map(obsgen):
    """
    class to process AVISO SLA map netcdf files into ROMS observation
    files. This is a subclass of seapy.roms.genobs.genobs, and handles
    the loading of the data.
    """
    def __init__(self, grid, dt, reftime=seapy.default_epoch, ssh_mean=None,
                 ssh_error=0.05):
        if ssh_mean is not None:
            self.ssh_mean = seapy.convolve_mask(ssh_mean, ksize=5, copy=True)
        else:
            self.ssh_mean = None
        self.ssh_error = ssh_error
        super().__init__(grid, dt, epoch)

    def convert_file(self, file, title="AVISO Obs"):
        """
        Load an AVISO file and convert into an obs structure
        """
        # Load AVISO Data
        nc = netCDF4.Dataset(file)
        lon = nc.variables["lon"][:]
        lat = nc.variables["lat"][:]
        dat = np.squeeze(nc.variables["sla"][:])
        err = np.squeeze(nc.variables["err"][:])
        time = netCDF4.num2date(nc.variables["time"][0],
                                nc.variables["time"].units) - self.epoch
        time = time.total_seconds() * seapy.secs2day
        nc.close()
        lon, lat = np.meshgrid(lon, lat)
        lat = lat.flatten()
        lon = lon.flatten()
        if not self.grid.east():
            lon[lon>180] -= 360
        data = [seapy.roms.obs.raw_data("ZETA", "SSH_AVISO_MAP",
                                dat.flatten(), err.flatten(), self.ssh_error)]
        # Grid it
        obs = seapy.roms.obs.gridder(self.grid, time, lon, lat, None,
                                     data, self.dt, title)

        # Apply the model mean ssh to the sla data
        if self.ssh_mean is not None:
            m, p = seapy.oasurf(self.grid.I, self.grid.J, self.ssh_mean,
                               obs.x, obs.y, nx=1, ny=1, weight=7)
            obs.value += m
        return obs

class ostia_sst_map(obsgen):
    """
    class to process OSTIA SST map netcdf files into ROMS observation
    files. This is a subclass of seapy.roms.genobs.genobs, and handles
    the loading of the data.
    """
    def __init__(self, grid, dt, reftime=seapy.default_epoch, temp_error=0.4,
                 temp_limits=None):
        self.temp_error = temp_error
        if temp_limits is None:
            self.temp_limits = (2,35)
        else:
            self.temp_limits = temp_limits
        super().__init__(grid, dt, epoch)

    def convert_file(self, file, title="OSTIA SST Obs"):
        """
        Load an OSTIA file and convert into an obs structure
        """
        # Load OSTIA Data
        nc = netCDF4.Dataset(file)
        lon = nc.variables["lon"][:]
        lat = nc.variables["lat"][:]
        dat = np.ma.masked_outside(np.squeeze(
                    nc.variables["analysed_sst"][:]) - 273.15,
                    self.temp_limits[0], self.temp_limits[1])
        err = np.squeeze(nc.variables["analysis_error"][:])
        time = netCDF4.num2date(nc.variables["time"][0],
                                nc.variables["time"].units) - self.epoch
        time = time.total_seconds() * seapy.secs2day
        nc.close()
        lon, lat = np.meshgrid(lon, lat)
        lat = lat.flatten()
        lon = lon.flatten()
        if not self.grid.east():
            lon[lon>180] -= 360

        data = [seapy.roms.obs.raw_data("TEMP", "SST_OSTIA", dat.flatten(),
                                        err.flatten(), self.temp_error)]
        # Grid it
        return seapy.roms.obs.gridder(self.grid, time, lon, lat, None,
                                     data, self.dt, title)

class seaglider_profile(obsgen):
    """
    class to process SeaGlider .pro files into ROMS observation
    files. This is a subclass of seapy.roms.genobs.genobs, and handles
    the loading of the data.
    """
    def __init__(self, grid, dt, reftime=seapy.default_epoch, temp_limits=None,
                 salt_limits=None, depth_limit=-15, temp_error=0.2,
                 salt_error=0.05):
        if temp_limits is None:
            self.temp_limits = (5, 30)
        else:
            self.temp_limits = temp_limits
        if salt_limits is None:
            self.salt_limits = (31, 35.5)
        else:
            self.salt_limits = salt_limits
        self.depth_limit = depth_limit
        self.temp_error = temp_error
        self.salt_error = salt_error
        super().__init__(grid, dt, epoch)

    def convert_file(self, file, title="SeaGlider Obs"):
        """
        Load a SeaGlider .pro file and convert into an obs structure
        """
        import re

        dtype = { 'names': ('time','pres','depth','temp','cond',
                            'salt','sigma','lat','lon'),
                  'formats': ['f4']*9 }

        # Load the text file. All data goes into the pro dictionary
        # as defined by dtype. The header information needs to be parsed
        with open(file) as myfile:
            header = [ myfile.readline() for i in range(19) ]
            pro = np.loadtxt(myfile, dtype, delimiter=',', comments='%')

        # Parse the header information
        parser = re.compile('^%(\w+): (.*)$')
        params = {}
        for line in header:
            try:
                opt = parser.findall(line)
                params[opt[0][0]] = opt[0][1]
            except:
                pass

        # Determine the needed information from the headers
        glider_name = "GLIDER" if params.get("glider", None) is None else \
                      "GLIDER_SG"+params["glider"]
        provenance = seapy.roms.obs.asprovenance(glider_name)
        try:
            date = [ int(s) for s in re.findall('([\d]{2})\s', params["start"]) ]
            start_time = datetime.datetime.strptime(params["start"].strip(),
                                                    "%m %d 1%y %H %M %S")
            dtime = (start_time - self.epoch).total_seconds()/86400
        except:
            raise ValueError("date format incorrect in file: "+file)

        # Make sure that the GPS fix isn't screwy
        if self.grid.east():
            pro["lon"][pro["lon"]<0] += 360
        dist = seapy.earth_distance(pro["lon"][0], pro["lat"][0],
                                    pro["lon"][-1], pro["lat"][-1])
        velocity = dist / pro["time"][-1]
        if velocity > 2:
            warn("WARNING: GPS fix is incorrect for "+file)
            return None

        # Build the data with masked entries
        temp = np.ma.masked_outside(pro["temp"], self.temp_limits[0],
                                    self.temp_limits[1])
        salt = np.ma.masked_outside(pro["salt"], self.salt_limits[0],
                                    self.salt_limits[1])
        depth = np.ma.masked_greater(-pro["depth"], self.depth_limit)

        # Grid it
        data = [ seapy.roms.obs.raw_data("TEMP", provenance, temp,
                                         None, self.temp_error),
                 seapy.roms.obs.raw_data("SALT", provenance, salt,
                                         None, self.salt_error)]
        return seapy.roms.obs.gridder(self.grid, pro["time"]/86400+dtime,
                                      pro["lon"], pro["lat"], depth,
                                      data, self.dt, title)


class tao_mooring(obsgen):
    """
    class to process TAO files into ROMS observation
    files. This is a subclass of seapy.roms.genobs.genobs, and handles
    the loading of the data.
    """
    def __init__(self, grid, dt, reftime=seapy.default_epoch, temp_limits=None,
                 salt_limits=None, u_limits=None, v_limits=None,
                 depth_limit=0, temp_error=0.25, salt_error=0.08,
                 u_error=0.08, v_error=0.08):
        if temp_limits is None:
            self.temp_limits = (5, 30)
        else:
            self.temp_limits = temp_limits
        if salt_limits is None:
            self.salt_limits = (31, 35.5)
        else:
            self.salt_limits = salt_limits
        if u_limits is None:
            self.u_limits = (-3, 3)
        else:
            self.u_limits = u_limits
        if v_limits is None:
            self.v_limits = (-3, 3)
        else:
            self.v_limits = v_limits
        self.depth_limit = depth_limit
        self.temp_error = temp_error
        self.salt_error = salt_error
        self.u_error = u_error
        self.v_error = v_error
        super().__init__(grid, dt, epoch)

    def convert_file(self, file, title="TAO Obs"):
        """
        Load a TAO netcdf file and convert into an obs structure
        """
        vals = {"temp": ["T_20", "QT_5020"],
                "salt": ["S_41", "QS_5041"],
                "u": ["U_320", "QS_5300"],
                "v": ["V_321", "QS_5300"]}
        nc = netCDF4.Dataset(file)
        lat = nc.variables["lat"][:]
        lon = nc.variables["lon"][:]
        if not self.grid.east():
            lon[lon>180] -= 360
        lat, lon = np.meshgrid(lat, lon)
        time = netCDF4.num2date(nc.variables["time"][:],
                                nc.variables["time"].units) - self.epoch
        time = list(map(lambda x: x.total_seconds()*seapy.secs2day, time))
        depth = -nc.variables["depth"][:]
        profile_list = np.where(np.logical_and.reduce((
                    lon >= np.min(self.grid.lon_rho),
                    lon <= np.max(self.grid.lon_rho),
                    lat >= np.min(self.grid.lat_rho),
                    lat <= np.max(self.grid.lat_rho))))

        # If nothing is in the area, return nothing
        if not profile_list[0].size:
            return None

        profile_list = (np.array(profile_list[0][4:6]),np.array(profile_list[1][4:6]))

        # Process each of the variables that are present
        obsdata = []
        for field in vals:
            limit = getattr(self, field+'_limits')
            if vals[field][0] in nc.variables:
                data = nc.variables[vals[field][0]][:]
                data = np.ma.masked_outside( \
                         data[profile_list[0], profile_list[1], :, :],
                         limit[0], limit[1], copy=False)
                qc = nc.variables[vals[field][1]][:]
                qc = qc[profile_list[0], profile_list[1], :, :]
                bad = np.where(np.logical_and(qc != 1, qc != 2))
                data[bad] = np.ma.masked
                obsdata.append(seapy.roms.obs.raw_data(field, "TAO_ARRAY",
                               data.compressed(), None,
                               getattr(self, field+'_error')))
        nc.close()


        # Build the time, lon, lat, and depth arrays of appropriate size
        npts = profile_list[0].size
        ndep = depth.size
        nt = len(time)
        lat = np.resize(lat[profile_list], (nt, ndep, npts))
        lat = np.squeeze(np.transpose(lat, (2, 1, 0)))[~data.mask]
        lon = np.resize(lon[profile_list], (nt, ndep, npts))
        lon = np.squeeze(np.transpose(lon, (2, 1, 0)))[~data.mask]
        depth = np.resize(depth, (npts, nt, ndep))
        depth = np.squeeze(np.transpose(depth, (0, 2, 1)))[~data.mask]
        time = np.squeeze(np.resize(time, (npts, ndep, nt)))[~data.mask]
        return seapy.roms.obs.gridder(self.grid, time, lon, lat, depth,
                                      obsdata, self.dt, title)
