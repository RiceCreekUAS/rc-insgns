"""WRAP_NAV_FILTER.PY
This script plays `.mat` flight data through navigation filter C-Code.
Both baseline navigation and researchNavigation are compiled into `.so` 
shared objects and wrapped in Python.  

This code automatically calls necessary terminal calls to gcc to compile
the functions.  However, more details on compiling the C-Code to make the 
`.so` manually can be found in `README.md`.

A set of customizable input flags are defined at the start of the script.

**Note:** Rerunning this in interactive mode has unexpected results!
          It doesn't seem to reload the latest `.so`.  This script
          should be called from the terminal.  For example:
          >> python wrap_nav_filter.py

Author: Hamid M.
Last Update: April 22, 2015
"""

import numpy as np

# # # # # START INPUTS # # # # #

FLAG_UNBIASED_IMU = False             # Choose if accel/gyro should be bias-free.
MAT_FILENAME = 'thor_flight75_WaypointTracker_150squareWaypointNew_2012_10_10.mat'
#MAT_FILENAME = 'flightdata_595.4961sec.mat'
T_GPSOFF = 350          # Time, above which, mission->haveGPS set to 0.
                        # To always keep GPS, set to: -1
FLAG_FORCE_INIT = True  # If True, will force the position and orientation estimates
                        # to initialize using the logged INS/GPS results from the `.mat`
                        # data file.
FLAG_PLOT_ATTITUDE = True
FLAG_PLOT_GROUNDTRACK = True
FLAG_PLOT_ALTITUDE = True
FLAG_PLOT_WIND     = True
# FLAG_PLOT_HAVEGPS  = True
FLAG_PLOT_SIGNALS  = True
SIGNAL_LIST = [0, 1, 8]  # List of signals [0 to 9] to be plotted
FLAG_WRITE2CSV = False # Write results to CSV file.
# # # # # END INPUTS # # # # #

import os, sys
join = os.path.join

import navigation
nav1 = navigation.filter()

import magnav
nav2 = magnav.filter()

import pydefs
insgps = pydefs.INSGPS(0, 0.0, np.zeros(3), np.zeros(3), np.zeros(3),
                       np.zeros(3), np.zeros(3), np.eye(15), np.zeros(6))
insgps_mag = pydefs.INSGPS(0, 0.0, np.zeros(3), np.zeros(3), np.zeros(3),
                           np.zeros(3), np.zeros(3), np.eye(15), np.zeros(6))

# simpler python structures for sensor input and filter output
import pydefs

# Import modules including the numpy and scipy.  Matplotlib is used for plotting results.
import os
import csv
import numpy as np
from scipy import io as sio
from matplotlib import pyplot as plt
import navpy
r2d = np.rad2deg

class dict2struct():
    pass

# Directory to converted flight data that contains the flight_data and flight_info structures
directory = 'flight_data'

# Name of .mat file that exists in the directory defined above and has the flight_data and flight_info structures
filepath = directory+os.sep+MAT_FILENAME

# Load Flight Data: ## IMPORTANT to have the .mat file in the flight_data and flight_info structures for this function ##
data = sio.loadmat(filepath, struct_as_record=False, squeeze_me=True)
print 'Loaded Data Summary'
print '* File: %s' % filepath.split(os.path.sep)[-1]
try:
    flight_data, flight_info = data['flight_data'], data['flight_info']
    print('* Date: %s' % flight_info.date)
    print('* Aircraft: %s' % flight_info.aircraft)
except KeyError:
    print 'KeyError'
    # Convert from Python dictionary to struct-like before
    flight_data = dict2struct()
    for k in data:
        exec("flight_data.%s = data['%s']" % (k, k))
del(data)

# Add both names for pitch: the and theta
try:
    flight_data.theta = flight_data.the
except AttributeError:
    pass

# Fill in time data
t = flight_data.time

# Magnetometer data - not used hence don't trust
hm  = np.vstack((flight_data.hx, -flight_data.hy, -flight_data.hz)).T

# Populate IMU Data
imu = np.vstack((t, flight_data.p, flight_data.q, flight_data.r, 
                 flight_data.ax, flight_data.ay, flight_data.az,
                 hm[:,0], hm[:,1], hm[:,2])).T

# Note that accelerometer and gyro measurements logged by UAV
# after 11/17/2011 flight (seemingly, see 
# http://trac.umnaem.webfactional.com/wiki/FlightReports/2011_11_17)
# have the nav-estimated bias removed before datalogging. So to work with raw
# imu-data, we add back the on-board estimated biases.
if not FLAG_UNBIASED_IMU:
    try:
        imu[:, 1:4] += np.vstack((flight_data.p_bias, 
                                  flight_data.q_bias, 
                                  flight_data.r_bias)).T

        imu[:, 4:7] += np.vstack((flight_data.ax_bias,
                                  flight_data.ay_bias,
                                  flight_data.az_bias)).T
    except AttributeError:
        print('Note: On board estimated bias not found.')

# Air Data
ias = flight_data.ias # indicated airspeed (m/s)
h = flight_data.h

# Populate GPS sensor data
try:
    vn = flight_data.gps_vn
except:
    vn = flight_data.vn
try:
    ve = flight_data.gps_ve
except:
    ve = flight_data.ve
try:
    vd = flight_data.gps_vd
except:
    vd = flight_data.vd
lat = flight_data.lat
lon = flight_data.lon
alt = flight_data.alt

# kstart set to when the navigation filter used onboard the aircraft
# was initialized and this is accomplished by detecting when navlat is
# no longer 0.0. This choice of kstart will ensure the filter being
# tested is using the same initialization time step as the onboard
# filter allowing for apples to apples comparisons.
kstart = (abs(flight_data.navlat) > 0.0).tolist().index(True)
k = kstart
print('Initialized at Time: %.2f s (k=%i)' % (t[k], k))

# Set previous value of GPS altitude to 0.0. This will be used to
# trigger GPS newData flag which is commonly used in our navigation
# filters for deciding if the GPS data has been updated. However, in
# python we have no log of newData (typically). So a comparison of
# current GPS altitude to the previous epoch's GPS altitude is used to
# determine if GPS has been updated.
last_gps_alt = -9999.9

# Values (Calculated by compiled test navigation filter) need to be
# stored in python variables and they need to be in the globaldefs.c
# and cdefs.py to allow for pulling them out and saving. These
# python variables need to be initialized to work properly in the
# while loop.
nav_data_dict = {}
nav_mag_data_dict = {}
haveGPS_store = []
t_store = []

def store_data(data_dict, insgps):
    """
    Append current elements from `insgps` into
    `data_dict`.  
    """
    # Initialize dictionary if needed (e.g.) first iteration.
    if len(data_dict) == 0:
        data_dict['psi_store'] = []
        data_dict['psi_store'] = []
        data_dict['the_store'] = []
        data_dict['phi_store'] = []
        data_dict['navlat_store'] = []
        data_dict['navlon_store'] = []
        data_dict['navalt_store'] = []
        data_dict['navStatus_store'] = []
        data_dict['wn_store'] = []
        data_dict['we_store'] = []
        data_dict['wd_store'] = []
        data_dict['signal_store'] = []

        data_dict['ax_bias'] = []
        data_dict['ay_bias'] = [] 
        data_dict['az_bias'] = []
        data_dict['p_bias'] = []
        data_dict['q_bias'] = []
        data_dict['r_bias'] = []

        data_dict['NS_std'] = []
        data_dict['WE_std'] = []
        data_dict['alt_std'] = []

        # Attitude errors (small angle errors about N-E-D)
        # Note: epsN and epsE are in general different than roll, pitch uncertainty.  
        data_dict['epsN_std'] = []
        data_dict['epsE_std'] = []
        data_dict['epsD_std'] = [] # yaw uncertainty [rad]

    # Store data
    data_dict['psi_store'].append(insgps.estATT[0])
    data_dict['the_store'].append(insgps.estATT[1])
    data_dict['phi_store'].append(insgps.estATT[2])
    data_dict['navlat_store'].append(insgps.estPOS[0])
    data_dict['navlon_store'].append(insgps.estPOS[1])
    data_dict['navalt_store'].append(insgps.estPOS[2])
    data_dict['navStatus_store'].append(insgps.valid) #fixme: was err_type
    #data_dict['wn_store'].append(insgps.wn)
    #data_dict['we_store'].append(insgps.we)
    #data_dict['wd_store'].append(insgps.wd)
    #data_dict['signal_store'].append([insgps.signal_0, insgps.signal_1,
    #                                  insgps.signal_2, insgps.signal_3,
    #                                  insgps.signal_4, insgps.signal_5, 
    #                                  insgps.signal_6, insgps.signal_7,
    #                                  insgps.signal_8, insgps.signal_9])

    data_dict['ax_bias'].append(insgps.estAB[0])
    data_dict['ay_bias'].append(insgps.estAB[1])
    data_dict['az_bias'].append(insgps.estAB[2])
    data_dict['p_bias'].append(insgps.estGB[0])
    data_dict['q_bias'].append(insgps.estGB[1])
    data_dict['r_bias'].append(insgps.estGB[2])

    data_dict['NS_std'].append(np.sqrt(insgps.P[0]))
    data_dict['WE_std'].append(np.sqrt(insgps.P[1]))
    data_dict['alt_std'].append(np.sqrt(insgps.P[2]))

    data_dict['epsN_std'].append(np.sqrt(insgps.P[6]))
    data_dict['epsE_std'].append(np.sqrt(insgps.P[7]))
    data_dict['epsD_std'].append(np.sqrt(insgps.P[8])) # yaw uncertainty [rad]

    return data_dict

# create data structures for ekf processing
imu_data = []
gps_data = []
k = 0
while k < len(t):
    p, q, r = imu[k, 1:4]
    ax, ay, az = imu[k, 4:7]
    hx, hy, hz = imu[k, 7:10]
    imu_pt = pydefs.IMU(t[k], 0, p, q, r, ax, ay, az, hx, hy, hz, 15.0)
    imu_data.append(imu_pt)
    
    if abs(alt[k] - last_gps_alt) > 0.0001:
        last_gps_alt = alt[k]
        gps_pt = pydefs.GPS(t[k], 0, t[k], lat[k], lon[k], alt[k], vn[k], ve[k], vd[k])
        gps_data.append(gps_pt)

    k += 1
print "imu records:", len(imu_data)
print "gps records:", len(gps_data)

# Using while loop starting at k (set to kstart) and going to end of .mat file
k = kstart
gps_index = 0
new_gps = 0
for k, imupt in enumerate(imu_data):
    # walk the gps counter forward as needed
    if imupt.time >= gps_data[gps_index].time:
        gps_index += 1
        new_gps = 1
    else:
        new_gps = 0
    if gps_index >= len(gps_data):
        # no more gps data, stay on the last record
        gps_index = len(gps_data)-1
    gpspt = gps_data[gps_index-1]
    gpspt.newData = new_gps
    #print "t(imu) = " + str(imupt.time) + " t(gps) = " + str(gpspt.time)

    # If k is at the initialization time init_nav else get_nav
    if k == kstart:
        insgps = nav1.init(imupt, gpspt)
        insgps_mag = nav2.init(imupt, gpspt)

        if FLAG_FORCE_INIT:
            # Force initial values to match logged INS/GPS result
            insgps.psi = flight_data.psi[k]
            insgps.the = flight_data.theta[k]
            insgps.phi = flight_data.phi[k]

            insgps.lat = flight_data.navlat[k] # Note: should be radians
            insgps.lon = flight_data.navlon[k] # Note: should be radians
            insgps.alt = flight_data.navalt[k]
            
            insgps_mag.psi = flight_data.psi[k]
            insgps_mag.the = flight_data.theta[k]
            insgps_mag.phi = flight_data.phi[k]

            insgps_mag.lat = flight_data.navlat[k] # Note: should be radians
            insgps_mag.lon = flight_data.navlon[k] # Note: should be radians
            insgps_mag.alt = flight_data.navalt[k]
    elif k > kstart:
        insgps = nav1.update(imupt, gpspt)
        insgps_mag = nav2.update(imupt, gpspt)

    # Store the desired results obtained from the compiled test navigation filter
    # and the baseline filter
    if k >= kstart:
        nav_data_dict = store_data(nav_data_dict, insgps)
        nav_mag_data_dict = store_data(nav_mag_data_dict, insgps_mag)
        # haveGPS_store.append(mission.haveGPS)
        t_store.append(imupt.time)

    # Increment time up one step for the next iteration of the while loop.    
    k += 1

# When k = len(t) execute the close_nav function freeing up memory from matrices.
nav1.close()
nav2.close()

# Plotting
if FLAG_PLOT_ATTITUDE:
    fig, [ax1, ax2, ax3] = plt.subplots(3,1)

    # Yaw Plot
    psi_nav = nav_data_dict['psi_store']
    psi_nav_mag = nav_mag_data_dict['psi_store']
    ax1.set_title(MAT_FILENAME, fontsize=10)
    ax1.set_ylabel('YAW (DEGREES)', weight='bold')
    ax1.plot(t_store, r2d(psi_nav), label='nav', c='k', lw=3, alpha=.5)
    ax1.plot(t_store, r2d(psi_nav_mag), label='nav_mag',c='blue', lw=2)
    ax1.plot(t[kstart:len(t)], r2d(flight_data.psi[kstart:len(t)]), label='On-Board', c='green', lw=2, alpha=.5)
    ax1.grid()
    ax1.legend(loc=0)

    # Pitch PLot
    the_nav = nav_data_dict['the_store']
    the_nav_mag = nav_mag_data_dict['the_store']  
    ax2.set_ylabel('PITCH (DEGREES)', weight='bold')
    ax2.plot(t_store, r2d(the_nav), label='nav', c='k', lw=3, alpha=.5)
    ax2.plot(t_store, r2d(the_nav_mag), label='nav_mag',c='blue', lw=2)
    ax2.plot(t[kstart:len(t)], r2d(flight_data.theta[kstart:len(t)]), label='On-Board', c='green', lw=2, alpha=.5)
    ax2.grid()

    # Roll PLot
    phi_nav = nav_data_dict['phi_store']
    phi_nav_mag = nav_mag_data_dict['phi_store']   
    ax3.set_ylabel('ROLL (DEGREES)', weight='bold')
    ax3.plot(t_store, r2d(phi_nav), label='nav', c='k', lw=3, alpha=.5)
    ax3.plot(t_store, r2d(phi_nav_mag), label='nav_mag', c='blue',lw=2)
    ax3.plot(t[kstart:len(t)], r2d(flight_data.phi[kstart:len(t)]), label='On-Board', c='green', lw=2, alpha=.5)
    ax3.set_xlabel('TIME (SECONDS)', weight='bold')
    ax3.grid()

# Altitude Plot
if FLAG_PLOT_ALTITUDE:
    navalt = nav_data_dict['navalt_store']
    nav_magalt = nav_mag_data_dict['navalt_store']
    plt.figure()
    plt.title('ALTITUDE')
    plt.plot(t[kstart:len(t)], flight_data.alt[kstart:len(t)], label='GPS Sensor', c='green', lw=3, alpha=.5)
    plt.plot(t[kstart:len(t)], flight_data.navalt[kstart:len(t)], label='On-Board', c='green', lw=2, alpha=.5)
    plt.plot(t_store, navalt, label='nav', c='k', lw=3, alpha=.5)
    plt.plot(t_store, nav_magalt, label='nav_mag',c='blue', lw=2)
    plt.ylabel('ALTITUDE (METERS)', weight='bold')
    plt.legend(loc=0)
    plt.grid()

# Wind Plot
#if FLAG_PLOT_WIND:
#    wn = nav_mag_data_dict['wn_store']
#    we = nav_mag_data_dict['we_store']
#    wd = nav_mag_data_dict['wd_store']
#    plt.figure()
#    plt.title('WIND ESTIMATES - Only from nav_mag')
#    plt.plot(t_store, wn, label='North',c='gray', lw=2)
#    plt.plot(t_store, we, label='East',c='black', lw=2)
#    plt.plot(t_store, wd, label='Down',c='blue', lw=2)
#    plt.ylabel('WIND (METERS/SECOND)', weight='bold')
#    plt.legend(loc=0)
#    plt.grid()

# Top View (Longitude vs. Latitude) Plot
if FLAG_PLOT_GROUNDTRACK:
    navlat = nav_data_dict['navlat_store']
    navlon = nav_data_dict['navlon_store']
    nav_maglat = nav_mag_data_dict['navlat_store']
    nav_maglon = nav_mag_data_dict['navlon_store']
    plt.figure()
    plt.title(MAT_FILENAME, fontsize=10)
    plt.ylabel('LATITUDE (DEGREES)', weight='bold')
    plt.xlabel('LONGITUDE (DEGREES)', weight='bold')
    plt.plot(flight_data.lon[kstart:len(t)], flight_data.lat[kstart:len(t)], '*', label='GPS Sensor', c='red', lw=2, alpha=.5)
    plt.plot(r2d(flight_data.navlon[kstart:len(t)]), r2d(flight_data.navlat[kstart:len(t)]), label='On-Board', c='green', lw=1, alpha=.85)
    plt.plot(r2d(navlon), r2d(navlat), label='nav', c='k', lw=3, alpha=.5)
    plt.plot(r2d(nav_maglon), r2d(nav_maglat), label='nav_mag', c='blue', lw=2)
    plt.grid()
    plt.legend(loc=0)

# if FLAG_PLOT_SIGNALS:
#     plt.figure()
#     plt.title('SIGNAL PLOTS - Only from nav_mag')
#     signal_store = nav_mag_data_dict['signal_store']
#     signal_store = np.array(signal_store)
#     for sig in SIGNAL_LIST:
#         plt.plot(t_store, signal_store[:,sig], label=str(sig), lw=2, alpha=.5)
#     plt.ylabel('SIGNAL UNITS', weight='bold')
#     plt.legend(loc=0)
#     plt.grid()

# haveGPS Plot
# if FLAG_PLOT_HAVEGPS:
#   plt.figure()
#   plt.title('MISSION HAVEGPS FLAG')
#   plt.plot(t_store, haveGPS_store, c='black', lw=2)
#   plt.ylim([-2,2])
#   plt.grid()

plt.show()


# Save Results to CSV File
if FLAG_WRITE2CSV:
    OUTPUT_FILENAME = filepath + '_postprocess.csv'
    hdr_list = ['OMAP Timestamp (microseconds since epoch)', 
                'Lat (1e-7 deg)', 'Lon (1e-7 deg)', 'Alt (m)',
                'Aircraft Roll (1e-4 rad)', 'Aircraft Pitch (1e-4 rad)', 'Aircraft Yaw (1e-4 rad)',
                'North-South std (m)', 'West-East std (m)', 'Alt std (m)',
                'Yaw std (deg)', 'Pitch std (deg)', 'Roll std (deg)']
    with open(OUTPUT_FILENAME, 'w') as fobj:
        # TODO: Print header
        csv_writer = csv.writer(fobj)
        csv_writer.writerow(hdr_list)
        for k in range(len(t_store)):
            # Convert eps_NED to eps_YPR
            yaw_rad   = nav_data_dict['psi_store'][k]
            pitch_rad = nav_data_dict['the_store'][k]
            roll_rad  = nav_data_dict['phi_store'][k]

            # Note, as part of transformation we are
            # ignoring uncertinty in the mapping.
            epsNED_std_deg = [r2d(nav_data_dict['epsN_std'][k]),
                              r2d(nav_data_dict['epsE_std'][k]),
                              r2d(nav_data_dict['epsD_std'][k])]
            yaw_std_deg = epsNED_std_deg[2]
            pitch_std_deg = navpy.angle2dcm(yaw_rad, 0, 0, input_unit='rad').dot(epsNED_std_deg)[1]
            roll_std_deg = navpy.angle2dcm(yaw_rad, pitch_rad, 0, input_unit='rad').dot(epsNED_std_deg)[0]

            row = [int(t_store[k]*1e6), 
                   int(r2d(nav_data_dict['navlat_store'][k])*1e7),
                   int(r2d(nav_data_dict['navlon_store'][k])*1e7),
                   nav_data_dict['navalt_store'][k],
                   int(roll_rad*1e4),
                   int(pitch_rad*1e4),
                   int(yaw_rad*1e4),
                   nav_data_dict['NS_std'][k],
                   nav_data_dict['WE_std'][k],
                   nav_data_dict['alt_std'][k],
                   yaw_std_deg,
                   pitch_std_deg,
                   roll_std_deg]
            csv_writer.writerow(row)
    print("Playback results written to: %s" % OUTPUT_FILENAME)