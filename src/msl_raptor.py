#!/usr/bin/env python3

# IMPORTS
# system
import os, sys, argparse, time
import pdb
# from pathlib import Path
# save/load
# import pickle
# math
import numpy as np
# plots
# import matplotlib
# from matplotlib import pyplot as plt
# from mpl_toolkits import mplot3d
# ros
import rospy
# custom modules
from ros_interface import ros_interface as ROS
from ukf import UKF
# libs & utils
from utils_msl_raptor.ros_utils import *
from utils_msl_raptor.math_utils import *
# sys.path.append('/root/msl_raptor_ws/src/msl_raptor/src/front_end/')
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + '/src/front_end')
from image_segmentor import ImageSegmentor


def run_execution_loop():
    b_use_gt_bb = True
    if b_use_gt_bb:
        rospy.logwarn("\n\n\n------------- IN DEBUG MODE (Using Ground Truth Bounding Boxes) -------------\n\n\n")
        time.sleep(0.5)
    rate = rospy.Rate(100) # max filter rate
    b_target_in_view = True
    ros = ROS(b_use_gt_bb)  # create a ros interface object
    wait_intil_ros_ready(ros, rate)  # pause to allow ros to get initial messages
    ukf = UKF()  # create ukf object
    if b_use_gt_bb:
        img_set = None
    else:
        print('initializing image segmentor!!!!!!')
        img_set = ImageSegmentor()
    init_objects(ros, ukf)  # init camera, pose, etc
    pdb.set_trace()

    state_est = np.zeros((ukf.dim_state + ukf.dim_sig**2, ))
    loop_count = 0
    last_image_time = 0

    rospy.logwarn("FIXING OUR POSE!!")
    while not rospy.is_shutdown():
        # store data locally (so it doesnt get overwritten in ROS object)
        loop_time = ros.latest_time
        if loop_time <= last_image_time:
            # this means we dont have new data yet
            continue
        tf_ego_w = inv_tf(pose_to_tf(ros.pose_w_ego))
        if not b_use_gt_bb:
            abb = ros.latest_bb  # angled bounding box
        else:
            abb = ukf.predict_measurement(pose_to_state_vec(ros.tracked_quad_pose_gt), tf_ego_w)
        bb_method = ros.latest_bb_method  # 1 for detect network, -1 for tracking network

        dt = loop_time - last_image_time
        ukf.itr_time = loop_time
        ukf.step_ukf(abb, tf_ego_w, dt)  # update ukf
        last_image_time = loop_time  # this ensures we dont reuse the image
        
        ros.publish_filter_state(ukf.mu, ukf.itr_time, ukf.itr)  # send vector with time, iteration, state_est
        
        rate.sleep()
        loop_count += 1
        print(" ")  # print blank line to separate iteration output
    print("ENDED")


def init_objects(ros, ukf):
    # create camera object (see https://github.com/StanfordMSL/uav_game/blob/tro_experiments/ec_quad_sim/ec_quad_sim/param/quad3_trans.yaml)
    ukf.camera = camera(ros)

    # init ukf state
    rospy.logwarn('using ground truth to initialize filter!')
    ukf.mu = pose_to_state_vec(ros.tracked_quad_pose_gt) 
    # ukf.mu[0:3] += np.array([-2, .5, .5]) 

    # init 3d bounding box in quad frame
    half_length = rospy.get_param('~target_bound_box_l') / 2
    half_width = rospy.get_param('~target_bound_box_w') / 2
    half_height = rospy.get_param('~target_bound_box_h') / 2
    ukf.bb_3d = np.array([[ half_length, half_width, half_height, 1.],  # 1 front, left,  up (from quad's perspective)
                          [ half_length, half_width,-half_height, 1.],  # 2 front, right, up
                          [ half_length,-half_width,-half_height, 1.],  # 3 back,  right, up
                          [ half_length,-half_width, half_height, 1.],  # 4 back,  left,  up
                          [-half_length,-half_width, half_height, 1.],  # 5 front, left,  down
                          [-half_length,-half_width,-half_height, 1.],  # 6 front, right, down
                          [-half_length, half_width,-half_height, 1.],  # 7 back,  right, down
                          [-half_length, half_width, half_height, 1.]]) # 8 back,  left,  down


def wait_intil_ros_ready(ros, rate):
    """ pause until ros is ready or timeout reached """
    rospy.loginfo("waiting for ros...")
    while ros.latest_time is None:
        rate.sleep()
        continue
    rospy.loginfo("ROS is initialized!")


class camera:
    def __init__(self, ros):
        """
        K: camera intrinsic matrix 
        tf_cam_ego: camera pose relative to the ego_quad (fixed)
        fov_horz/fov_vert: Angular field of view (IN DEGREES) for horizontal and vertical directions
        """
        ns = rospy.get_param('~ns')
        camera_info = rospy.wait_for_message(ns + '/camera/camera_info', CameraInfo, 5)
        self.K = np.reshape(camera_info.K, (3, 3))
        self.tf_cam_ego = np.eye(4)
        self.tf_cam_ego[0:3, 3] = np.asarray(rospy.get_param('~t_cam_ego'))
        self.tf_cam_ego[0:3, 0:3] = np.reshape(rospy.get_param('~R_cam_ego'), (3, 3))
        self.fov_horz, self.fov_vert = self.calc_fov()

    def calc_fov(self):
        """
        - Find top, left point 1 meter along z axis in cam frame. the x and y values are 
        half the width and height. Note: [x_tl, y_tl, 1 (= z_tl)] = inv(K) @ [0, 0, 1], 
        which is just the first tow rows of the third col of inv(K).
        - With these x and y, just use geometry (knowing z dist is 1) to get the angle 
        spanning the x and y axis respectively.
        """
        return np.degrees(2 * np.arctan(-la.inv(self.K)[0:2, 2]))

    def pix_to_pnt3d(self, row, col):
        """
        input: assumes rc is [row, col]
        output: pnt_c = [x, y, z] in camera frame
        """
        pdb.set_trace()

        pnt_c = la.inv(self.K) @ np.array([col, row, 1])
        return pnt_c

    def pnt3d_to_pix(self, pnt_c):
        """
        input: assumes pnt in camera frame
        output: [row, col] i.e. the projection of xyz onto camera plane
        """
        rc = self.K @ np.reshape(pnt_c[0:3], 3, 1)
        rc = np.array([rc[1], rc[0]]) / rc[2]
        return rc


if __name__ == '__main__':
    np.set_printoptions(linewidth=160, suppress=True)  # format numpy so printing matrices is more clear
    print("Starting MSL-RAPTOR main [running python {}]".format(sys.version_info[0]))
    rospy.init_node('RAPTOR_MSL', anonymous=True)
    run_execution_loop()
    print("--------------- FINISHED ---------------")

