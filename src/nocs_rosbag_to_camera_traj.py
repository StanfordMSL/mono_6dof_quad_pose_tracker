
#!/usr/bin/env python3

# IMPORTS
# system
import os, sys, argparse, time
from copy import copy
from collections import defaultdict
import pdb
# math
import numpy as np
import cv2
# ros
import rospy
import rosbag
# custom modules
# from ros_interface import ros_interface as ROS
# from ukf import UKF
# libs & utils
# from utils_msl_raptor.ros_utils import *
from utils_msl_raptor.math_utils import *
from utils_msl_raptor.ukf_utils import state_to_tf, pose_to_3d_bb_proj, load_class_params
# sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + '/src/front_end')
# from image_segmentor import ImageSegmentor
import yaml
import pickle
import matplotlib
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D


class nocs_rb_to_ego_traj:
    def __init__(self):
        self.plot_traj_from_rb()
        # self.plot_traj_from_pkl()
        pdb.set_trace()
        print("DONE WITH INIT")


    def plot_traj_from_rb(self):
        self.rosbag_in_dir = "/mounted_folder/nocs/test"
        self.bag_in_name = "scene_1.bag"
        try:
            self.bag = rosbag.Bag(self.rosbag_in_dir + '/' + self.bag_in_name, 'r')
        except Exception as e:
            raise RuntimeError("Unable to Process Rosbag!!\n{}".format(e))

        print("Processing {}".format(self.bag_in_name))
        self.gt_txyz_w_ado = {}
        self.gt_txyz_w_ego = {}
        self.tf_w_ado_t0 = {}
        for i, (topic, msg, t) in enumerate(self.bag.read_messages()):
            t_split = topic.split("/")
            if not t_split[0] == 'quad7' and t_split[-1] == 'pose' and t_split[-2] == 'vision_pose': # ground truth from a quad / nocs
                name = t_split[0]
                if name in self.tf_w_ado_t0:
                    tf_ego_ado = ros_pose_to_tf(msg.pose)
                    tf_w_ego = self.tf_w_ado_t0[name] @ inv_tf(tf_ego_ado)
                    tf_w_ado = tf_w_ego @ tf_ego_ado
                    # tf_w_ego  = tf_ego_ado
                    self.gt_txyz_w_ego[name] = np.concatenate((self.gt_txyz_w_ego[name], 
                                                np.reshape([t.to_sec(), tf_w_ego[0, 3], tf_w_ego[1, 3], tf_w_ego[2, 3]], (1, 4))) )
                else: # first occurance
                    self.tf_w_ado_t0[name] = ros_pose_to_tf(msg.pose)
                    self.gt_txyz_w_ego[name] = np.reshape([t.to_sec(), 0, 0, 0], (1, 4))            

        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        line_color = {"bowl_white_small_norm": "k", "camera_canon_len_norm": "r", "can_arizona_tea_norm": "b", "laptop_air_xin_norm": "g", "mug_daniel_norm": "m"}
        for name in self.gt_txyz_w_ego.keys():
            # self.gt_txyz_w_ego[name].view('i8,i8,i8,i8').sort(order=['f1'], axis=0)  # sorts by first col - https://stackoverflow.com/questions/2828059/sorting-arrays-in-numpy-by-column
            # self.gt_txyz_w_ego[name][:, 0] -= self.gt_txyz_w_ego[name][0, 0]
            # plt_range = range(0,80)
            # ax.scatter(xs=self.gt_txyz_w_ego[name][:,1], ys=self.gt_txyz_w_ego[name][:,2], zs=self.gt_txyz_w_ego[name][:,3], color=line_color[name], marker='.')
            ax.plot(xs=self.gt_txyz_w_ego[name][:,1], ys=self.gt_txyz_w_ego[name][:,2], zs=self.gt_txyz_w_ego[name][:,3], color=line_color[name], linestyle='-')
            # ax.scatter(xs=self.gt_txyz_w_ado[name][plt_range,1], ys=self.gt_txyz_w_ado[name][plt_range,2], zs=self.gt_txyz_w_ado[name][plt_range,3], color=line_color[name], marker='.')
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.set_zlabel('Z')

        plt.show(block=False)


    def plot_traj_from_pkl(self):
        pickle_path = "/mounted_folder/test_graphs_gtsam/gts/real_test/results_real_test_scene_1_"
        self.gt_txyz_w_ego = {}
        self.tf_w_ado_t0 = {}
        for i in range(0, 389):
            fn = pickle_path + "{:04d}.pkl".format(i)
            try:
                with (open(fn, "rb")) as openfile:
                    data = pickle.load(openfile)
                    gt_RTs = data['gt_RTs']
                    for obj_idx in range(gt_RTs.shape[0]):
                        if not obj_idx in self.tf_w_ado_t0: # first iteration
                            self.tf_w_ado_t0[obj_idx] = gt_RTs[obj_idx]
                            self.gt_txyz_w_ego[obj_idx] = np.reshape([0, 0, 0], (1, 3)) 
                        else:
                            tf_ego_ado = gt_RTs[obj_idx]
                            tf_w_ego = self.tf_w_ado_t0[obj_idx] @ inv_tf(tf_ego_ado)
                            tf_w_ado = tf_w_ego @ tf_ego_ado
                            self.gt_txyz_w_ego[obj_idx] = np.concatenate((self.gt_txyz_w_ego[obj_idx], np.reshape([tf_w_ego[0, 3], tf_w_ego[1, 3], tf_w_ego[2, 3]], (1, 3))) )
            except:
                print("missing / skipping file: {:04d}.pkl".format(i))

        # pdb.set_trace()
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        # line_color = {"bowl_white_small_norm": "k", "camera_canon_len_norm": "r", "can_arizona_tea_norm": "b", "laptop_air_xin_norm": "g", "mug_daniel_norm": "m"}
        line_color = ['k', 'r', 'b', 'g', 'm']
        for obj_idx in self.gt_txyz_w_ego.keys():
            # ax.scatter(xs=self.gt_txyz_w_ego[obj_idx][:,0], ys=self.gt_txyz_w_ego[obj_idx][:,1], zs=self.gt_txyz_w_ego[obj_idx][:,2], color=line_color[obj_idx])
            ax.plot(xs=self.gt_txyz_w_ego[obj_idx][:,0], ys=self.gt_txyz_w_ego[obj_idx][:,1], zs=self.gt_txyz_w_ego[obj_idx][:,2], color=line_color[obj_idx], linestyle='-')
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.set_zlabel('Z')

        plt.show(block=False)



def ros_pose_to_tf(pose):
    state = np.zeros((13,))
    state[0] = pose.position.x
    state[1] = pose.position.y
    state[2] = pose.position.z
    state[6] = pose.orientation.w
    state[7] = pose.orientation.x
    state[8] = pose.orientation.y
    state[9] = pose.orientation.z
    return state_to_tf(state)

if __name__ == '__main__':
    try:
        nocs_rb_to_ego_traj()
        
    except:
        import traceback
        traceback.print_exc()
