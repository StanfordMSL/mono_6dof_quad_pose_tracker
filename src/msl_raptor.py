#!/usr/bin/env python

# IMPORTS
# system
import os, sys, argparse, time
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
    init_objects(ros, ukf)  # init camera, pose, etc

    rospy.logwarn('need actual 3d bounding box info')
    bb_3d = np.zeros((8, 3))

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
        rate.sleep()
        loop_count += 1


def init_objects(ros, ukf):
    # create camera object (see https://github.com/StanfordMSL/uav_game/blob/tro_experiments/ec_quad_sim/ec_quad_sim/param/quad3_trans.yaml)
    camera['K'] = ros.K  # camera intrinsic matrix is recieved via ros
    camera['tf_cam_ego'] = np.eye(4)

    tf_cam_ego = np.eye(4)
    tf_cam_ego[0:3, 3] = [0.05, 0.0, 0.07]
    tf_cam_ego[0:3, 0:3] = np.array([[ 0.0,  0.0,  1.0], 
                                     [-1.0,  0.0,  0.0], 
                                     [ 0.0, -1.0,  0.0]])
    ukf.camera = camera(ros.K, tf_cam_ego)

    # init ukf state
    rospy.logwarn('using ground truth to initialize filter!')
    ukf.mu = pose_to_state_vec(ros.quad_pose_gt)


def wait_intil_ros_ready(ros, timeout = 10):
    """ pause until ros is ready or timeout reached """
    start_time = rospy.Time.now().to_sec()
    time = rospy.Time.now().to_sec()
    while not ros.latest_time and (time - start_time < timeout):
        time = rospy.Time.now().to_sec()
        rospy.loginfo("waiting for ROS to be ready...")


class camera:
    def __init__(self, K, tf_cam_ego):
        self.K = K  # camera intrinsic matrix
        self.tf_cam_ego = tf_cam_ego  # camera pose relative to the quad (fixed)

    def pix_to_pnt3d(self):
        rospy.logwarn("pix_to_pnt3d is not written yet!")
        pass

    def pnt3d_to_pix(self, pnt_q):
        """
        input: assumes pnt in quad frame
        output: [row, col] i.e. the projection of xyz onto camera plane
        """
        pnt_c = self.tf_cam_ego @ np.concatinate((pnt_q, [1]))
        rc = camera.K @ np.reshape(pnt_c[0:3], 3, 1);
        rc = [rc(2), rc(1)] / rc(3);


if __name__ == '__main__':
    print("Starting MSL-RAPTOR main")
    rospy.init_node('RAPTOR_MSL', anonymous=True)
    run_execution_loop()
    print("--------------- FINISHED ---------------")

