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
from utils.ros_utils import *


def run_execution_loop():
    rate = rospy.Rate(100) # max filter rate
    b_target_in_view = True
    last_image_time = -1
    ros = ROS()  # create a ros interface object
    wait_intil_ros_ready(ros)  # pause to allow ros to get initial messages
    ukf = UKF()  # create ukf object
    bb_3d, last_image_time = init_objects(ros, ukf)  # init camera, pose, etc

    state_est = np.zeros((13, ))
    loop_count = 0
    while not rospy.is_shutdown():
        loop_time = ros.latest_time
        if loop_time <= last_image_time:
            # this means we dont have new data yet
            continue
        dt = loop_time - last_image_time
        # store data locally (so it doesnt get overwritten in ROS object)

        abb = ros.latest_bb  # angled bounding box
        ego_pose = ros.latest_ego_pose  # stored as a ros PoseStamped
        bb_aqq_method = ros.latest_bb_method  # 1 for detect network, -1 for tracking network

        rospy.loginfo("Recieved new image at time {:.4f}".format(ros.latest_time))
        # update ukf
        ukf.step_ukf(abb, bb_3d, pose_to_tf(ego_pose), dt)
        ros.publish_filter_state(np.concatenate(([loop_time], [loop_count], state_est)))  # send vector with time, iteration, state_est
        # [optional] update plots
        last_image_time = loop_time
        rate.sleep()
        loop_count += 1


def init_objects(ros, ukf):
    # create camera object (see https://github.com/StanfordMSL/uav_game/blob/tro_experiments/ec_quad_sim/ec_quad_sim/param/quad3_trans.yaml)
    ukf.camera = camera(ros)

    # init ukf state
    rospy.logwarn('using ground truth to initialize filter!')
    ukf.mu = pose_to_state_vec(ros.quad_pose_gt)
    init_time = 0

    # init 3d bounding box in quad frame
    half_length = rospy.get_param('~target_bound_box_l') / 2
    half_width = rospy.get_param('~target_bound_box_w') / 2
    half_height = rospy.get_param('~target_bound_box_h') / 2
    bb_3d = np.array([[ half_length, half_width, half_height, 1.],  # 1 front, left,  up (from quad's perspective)
                      [ half_length, half_width,-half_height, 1.],  # 2 front, right, up
                      [ half_length,-half_width,-half_height, 1.],  # 3 back,  right, up
                      [ half_length,-half_width, half_height, 1.],  # 4 back,  left,  up
                      [-half_length,-half_width, half_height, 1.],  # 5 front, left,  down
                      [-half_length,-half_width,-half_height, 1.],  # 6 front, right, down
                      [-half_length, half_width,-half_height, 1.],  # 7 back,  right, down
                      [-half_length, half_width, half_height, 1.]]) # 8 back,  left,  down
    return bb_3d, init_time


def wait_intil_ros_ready(ros, timeout = 10):
    """ pause until ros is ready or timeout reached """
    rospy.loginfo("waiting for ros...")
    while ros.latest_time is None or ros.quad_pose_gt is None or ros.latest_ego_pose is None:
        continue
    rospy.loginfo("done!")


class camera:
    def __init__(self, ros):
         # camera intrinsic matrix K and pose relative to the quad (fixed)
        self.K , self.tf_cam_ego = get_ros_camera_info()

    def pix_to_pnt3d(self):
        rospy.logwarn("pix_to_pnt3d is not written yet!")
        pass

    def pnt3d_to_pix(self, pnt_q):
        """
        input: assumes pnt in quad frame
        output: [row, col] i.e. the projection of xyz onto camera plane
        """
        pnt_c = self.tf_cam_ego @ np.concatenate((pnt_q, [1]))
        rc = self.K @ np.reshape(pnt_c[0:3], 3, 1)
        rc = np.array([rc[1], rc[0]]) / rc[2]
        return rc


if __name__ == '__main__':
    np.set_printoptions(linewidth=160)  # format numpy so printing matrices is more clear
    print("Starting MSL-RAPTOR main [running python {}]".format(sys.version_info[0]))
    rospy.init_node('RAPTOR_MSL', anonymous=True)
    run_execution_loop()
    print("--------------- FINISHED ---------------")

