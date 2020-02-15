#!/usr/bin/env python3
# IMPORTS
# system
import sys, os, time
from copy import copy
from collections import defaultdict
import yaml
import pdb
# math
import numpy as np
from scipy.spatial.transform import Rotation as R
# ros
import rosbag
import rospy
from geometry_msgs.msg import PoseStamped, Twist, Pose
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Float32MultiArray, MultiArrayDimension
from msl_raptor.msg import AngledBbox, AngledBboxes, TrackedObjects, TrackedObject
import tf
import cv2
from cv_bridge import CvBridge, CvBridgeError
# Utils
sys.path.append('/root/msl_raptor_ws/src/msl_raptor/src/utils_msl_raptor')
from ssp_utils import *
from math_utils import *
from ros_utils import *
from raptor_logger import *
from pose_metrics import *

class rosbags_to_logs:
    """
    This class takes in the rosbag that is output from mslraptor and processes it into our custom log format 
    this enables us to unify output with the baseline method)
    The code currently also runs the quantitative metric analysis in the processes, but this is optional and will be done 
    again in the result_analyser. 
    """
    def __init__(self, rb_name=None, data_source='raptor', ego_quad_ns="/quad7"):
        # Parse rb_name
        us_split = rb_name.split("_")
        if rb_name[-4:] == '.bag' or "_".join(us_split[0:3]) == 'msl_raptor_output':
            # This means rosbag name is one that was post-processed
            if len(rb_name) > 4 and rb_name[-4:] == ".bag":
                self.rb_name = rb_name
            else:
                self.rb_name = rb_name + ".bag"
        elif len(rb_name) > 4 and "_".join(us_split[0:4]) == 'rosbag_for_post_process':
            # we assume this is the rosbag that fed into raptor
            rb_name = "msl_raptor_output_from_bag_rosbag_for_post_process_" + us_split[4]
            if rb_name[-4:] == ".bag":
                self.rb_name = rb_name
            else:
                self.rb_name = rb_name + ".bag"
        else:
            raise RuntimeError("We do not recognize bag file! {} not understood".format(rb_name))
        
        self.rosbag_in_dir = "/mounted_folder/raptor_processed_bags"
        self.log_out_dir = "/mounted_folder/" + data_source + "_logs"

        try:
            self.bag = rosbag.Bag(self.rosbag_in_dir + '/' + self.rb_name, 'r')
        except Exception as e:
            raise RuntimeError("Unable to Process Rosbag!!\n{}".format(e))


        self.ado_est_topic   = ego_quad_ns + '/msl_raptor_state'  # ego_quad_ns since it is ego_quad's estimate of the ado quad
        self.bb_data_topic   = ego_quad_ns + '/bb_data'  # ego_quad_ns since it is ego_quad's estimate of the bounding box
        self.ego_gt_topic    = ego_quad_ns + '/mavros/vision_pose/pose'
        self.ego_est_topic   = ego_quad_ns + '/mavros/local_position/pose'
        self.cam_info_topic  = ego_quad_ns + '/camera/camera_info'
        self.topic_func_dict = {self.ado_est_topic : self.parse_ado_est_msg, 
                                self.bb_data_topic : self.parse_bb_msg,
                                self.ego_gt_topic  : self.parse_ego_gt_msg, 
                                self.ego_est_topic : self.parse_ego_est_msg,
                                self.cam_info_topic: self.parse_camera_info_msg}

        # Read yaml file to get object params
        self.ado_names = []  # names actually in our rosbag
        self.ado_names_all = []  # all names we could possibly recognize
        self.bb_3d_dict_all = defaultdict(list)  # turns the name into the len, width, height, and diam of the object
        self.tf_cam_ego = None
        self.read_yaml()


        self.fig = None
        self.axes = None
        self.name = 'mslquad'
        self.t0 = -1
        self.tf = -1
        self.t_est = defaultdict(list)
        self.t_gt = defaultdict(list)

        self.ego_gt_time_pose = []
        self.ego_gt_pose = []
        self.ego_est_pose = []
        self.ego_est_time_pose = []
        self.ado_gt_pose = defaultdict(list)
        self.ado_est_pose = defaultdict(list)
        self.ado_est_state = defaultdict(list)

        self.DETECT = 1
        self.TRACK = 2
        self.FAKED_BB = 3
        self.IGNORE = 4
        self.detect_time = []
        self.detect_mode = []
        
        # self.detect_times = {}
        # self.detect_end = None
        self.abb_list = defaultdict(list)
        self.abb_time_list = defaultdict(list)

        self.K = None
        self.dist_coefs = None
        self.new_camera_matrix = None
        #########################################################################################
        self.process_rb()
        
        base_path = self.log_out_dir + "/log_" + self.rb_name[:-4].split("_")[-1] 
        self.logger = raptor_logger(mode="write", names=self.ado_names, base_path=base_path)

        # self.diam = 0.311
        # self.box_length = 0.27
        # self.box_width = 0.27
        # self.box_height = 0.13

        self.raptor_metrics = pose_metric_tracker(px_thresh=5, prct_thresh=10, trans_thresh=0.05, ang_thresh=5, name=self.names, diams=self.bb_3d_dict_all)
        
        pdb.set_trace()
        self.convert_rosbag_info_to_log()
        self.logger.close_files()


    def convert_rosbag_info_to_log(self):
        N = len(self.t_est)
        print("Post-processing data now ({} itrs)".format(N))
        vertices = np.array([[ self.box_length/2, self.box_width/2, self.box_height/2, 1.],
                             [ self.box_length/2, self.box_width/2,-self.box_height/2, 1.],
                             [ self.box_length/2,-self.box_width/2,-self.box_height/2, 1.],
                             [ self.box_length/2,-self.box_width/2, self.box_height/2, 1.],
                             [-self.box_length/2,-self.box_width/2, self.box_height/2, 1.],
                             [-self.box_length/2,-self.box_width/2,-self.box_height/2, 1.],
                             [-self.box_length/2, self.box_width/2,-self.box_height/2, 1.],
                             [-self.box_length/2, self.box_width/2, self.box_height/2, 1.]]).T

        # Write params to log file ########
        log_data = {}
        if self.new_camera_matrix is not None:
            log_data['K'] = np.array([self.new_camera_matrix[0, 0], self.new_camera_matrix[1, 1], self.new_camera_matrix[0, 2], self.new_camera_matrix[1, 2]])
        else:
            log_data['K'] = np.array([self.K[0, 0], self.K[1, 1], self.K[0, 2], self.K[1, 2]])
        log_data['3d_bb_dims'] = np.array([self.box_length, self.box_width, self.box_height, self.diam])
        log_data['tf_cam_ego'] = np.reshape(self.tf_cam_ego, (16,))
        self.logger.write_data_to_log(log_data, mode='prms')
        ###################################

        for i in range(N):
            # extract data in form for logging
            t_est = self.t_est[i]
            tf_w_ado_est = pose_to_tf(self.ado_est_pose[i])

            pose_msg, _ = find_closest_by_time(t_est, self.ego_gt_time_pose, message_list=self.ego_gt_pose)
            tf_w_ego_gt = pose_to_tf(pose_msg)

            pose_msg, _ = find_closest_by_time(t_est, self.ego_est_time_pose, message_list=self.ego_est_pose)
            tf_w_ego_est = pose_to_tf(pose_msg)

            pose_msg, _ = find_closest_by_time(t_est, self.t_gt, message_list=self.ado_gt_pose)
            tf_w_ado_gt = pose_to_tf(pose_msg)

            tf_w_cam = tf_w_ego_gt @ inv_tf(self.tf_cam_ego)
            tf_cam_w = inv_tf(tf_w_cam)
            tf_cam_ado_est = tf_cam_w @ tf_w_ado_est
            tf_cam_ado_gt = tf_cam_w @ tf_w_ado_gt

            R_pr = tf_cam_ado_est[0:3, 0:3]
            t_pr = tf_cam_ado_est[0:3, 3].reshape((3, 1))
            tf_cam_ado_gt = tf_cam_w @ tf_w_ado_gt
            R_gt = tf_cam_ado_gt[0:3, 0:3]
            t_gt = tf_cam_ado_gt[0:3, 3].reshape((3, 1))
            
            ######################################################
            
            if self.raptor_metrics is not None:
                self.raptor_metrics.update_all_metrics(vertices=vertices, R_gt=R_gt, t_gt=t_gt, R_pr=R_pr, t_pr=t_pr, K=self.new_camera_matrix)

            # Write data to log file #############################
            (abb, im_seg_mode), _ = find_closest_by_time(t_est, self.abb_time_list, message_list=self.abb_list)
            log_data['time'] = t_est
            log_data['state_est'] = tf_to_state_vec(tf_w_ado_est)
            log_data['state_gt'] = tf_to_state_vec(tf_w_ado_gt)
            log_data['ego_state_est'] = tf_to_state_vec(tf_w_ego_est)
            log_data['ego_state_gt'] = tf_to_state_vec(tf_w_ego_gt)
            corners3D_pr = (tf_w_ado_est @ vertices)[0:3,:]
            corners3D_gt = (tf_w_ado_gt @ vertices)[0:3,:]
            log_data['corners_3d_est'] = np.reshape(corners3D_pr, (corners3D_pr.size,))
            log_data['corners_3d_gt'] = np.reshape(corners3D_gt, (corners3D_gt.size,))
            log_data['proj_corners_est'] = np.reshape(self.raptor_metrics.proj_2d_pr.T, (self.raptor_metrics.proj_2d_pr.size,))
            log_data['proj_corners_gt'] = np.reshape(self.raptor_metrics.proj_2d_gt.T, (self.raptor_metrics.proj_2d_gt.size,))
            log_data['abb'] = abb
            log_data['im_seg_mode'] = im_seg_mode
            self.logger.write_data_to_log(log_data, mode='est')
            self.logger.write_data_to_log(log_data, mode='gt')
            ######################################################

        if self.raptor_metrics is not None:
            self.raptor_metrics.calc_final_metrics()
            self.raptor_metrics.print_final_metrics()
        print("done processing rosbag into logs!")


    def process_rb(self):
        print("Processing {}".format(self.rb_name))
        for topic, msg, t in self.bag.read_messages():
            t_split = topic.split("/")
            name = t_split[0]
            if topic in self.topic_func_dict:
                self.topic_func_dict[topic](msg)
            elif name in self.ado_names and (t_split[-1] == 'pose' or t_split[-1] == 'msl_raptor_state'):
                # this means we are a tracked object
                if name not in self.ado_names:
                    self.ado_names.append(name)
                if t_split[-1] == 'pose': # ground truth
                    self.parse_ado_gt_msg(msg, name=name)
                if t_split[-1] == 'pose': # estimate truth
                    self.parse_ado_est_msg(msg)
        
        for n in self.t_est:
            self.t_est[n] = np.asarray(self.t_est[n])
            min_t = np.min(self.t_est[n])
            max_t = np.max(self.t_est[n])
            if self.t0 is None:
                self.t0 = min_t
            elif self.t0 > min_t:
                self.t0 = min_t
            if self.tf is None:
                self.tf = max_t - self.t0
            elif self.tf < (max_t - self.t0):
                self.tf = max_t - self.t0
        for n in self.t_est:
            self.t_est[n] -= self.t0
            self.t_gt[n] = np.asarray(self.t_gt[n]) - self.t0
        # self.detect_time[n] = np.asarray(self.detect_time) - self.t0
        # self.detect_times[n] = np.asarray(self.detect_times) - self.t0


    def read_yaml(self, yaml_path="/root/msl_raptor_ws/src/msl_raptor/params/all_obs.yaml"):
        with open(yaml_path, 'r') as stream:
            try:
                obj_prms = list(yaml.load_all(stream))
                for obj_dict in obj_prms:
                    if obj_dict['id'] < 0 or obj_dict['ns'] == self.ego_gt_topic.split("/")[0][1:]: 
                        # this means its the ego robot, dont add it to ado (but get its camera params)
                        self.tf_cam_ego = np.eye(4)
                        self.tf_cam_ego[0:3, 3] = np.asarray(obj_dict['t_cam_ego'])
                        self.tf_cam_ego[0:3, 0:3] = np.reshape(obj_dict['R_cam_ego'], (3, 3))
                        # Correct Rotation w/ Manual Calibration
                        Angle_x = 8./180. 
                        Angle_y = 8./180.
                        Angle_z = 0./180. 
                        R_deltax = np.array([[ 1.             , 0.             , 0.              ],
                                             [ 0.             , np.cos(Angle_x),-np.sin(Angle_x) ],
                                             [ 0.             , np.sin(Angle_x), np.cos(Angle_x) ]])
                        R_deltay = np.array([[ np.cos(Angle_y), 0.             , np.sin(Angle_y) ],
                                             [ 0.             , 1.             , 0               ],
                                             [-np.sin(Angle_y), 0.             , np.cos(Angle_y) ]])
                        R_deltaz = np.array([[ np.cos(Angle_z),-np.sin(Angle_z), 0.              ],
                                             [ np.sin(Angle_z), np.cos(Angle_z), 0.              ],
                                             [ 0.             , 0.             , 1.              ]])
                        R_delta = R_deltax @ R_deltay @ R_deltaz
                        self.tf_cam_ego[0:3, 0:3] = np.matmul(R_delta, self.tf_cam_ego[0:3, 0:3])
                    else:
                        name = obj_dict['ns']
                        self.ado_names_all.append(name)
                        l = float(obj_dict['bound_box_l']) 
                        w = float(obj_dict['bound_box_w'])
                        h = float(obj_dict['bound_box_h'])
                        if 'diam' in obj_dict:
                            d = obj_dict['diam']
                        else:
                            d = la.norm([l, w, h])
                        self.bb_3d_dict_all[name] = np.array([l, w, h, d])
            except yaml.YAMLError as exc:
                print(exc)


    def parse_camera_info_msg(self, msg, t=None):
        if self.K is None:
            camera_info = msg
            self.K = np.reshape(camera_info.K, (3, 3))
            if len(camera_info.D) == 5:
                self.dist_coefs = np.reshape(camera_info.D, (5,))
                self.new_camera_matrix, _ = cv2.getOptimalNewCameraMatrix(self.K, self.dist_coefs, (camera_info.width, camera_info.height), 0, (camera_info.width, camera_info.height))
            else:
                self.dist_coefs = None
                self.new_camera_matrix = self.K
    

    def parse_ado_est_msg(self, msg, t=None):
        """
        record estimated poses from msl-raptor
        """
        tracked_obs = msg.tracked_objects
        if len(tracked_obs) == 0:
            return
        # to = tracked_obs[0]  # assumes 1 object for now
        for to in tracked_obs:
            name = to.class_str
            if t is None:
                self.t_est[name].append(to.pose.header.stamp.to_sec())
            else:
                self.t_est[name].append(t)

            self.ado_est_pose[name].append(to.pose.pose)
            # self.ado_est_state[name].append(to.state)


    def parse_ado_gt_msg(self, msg, name, t=None):
        """
        record optitrack poses of tracked quad
        """
        if t is None:
            self.t_gt[name].append(msg.header.stamp.to_sec())
        else:
            self.t_gt[name].append(t)

        self.ado_gt_pose[name].append(msg.pose)

        
    def parse_ego_gt_msg(self, msg, t=None):
        """
        record optitrack poses of tracked quad
        """
        self.ego_gt_pose.append(msg.pose)
        self.ego_gt_time_pose.append(msg.header.stamp.to_sec())

        
    def parse_ego_est_msg(self, msg, t=None):
        """
        record optitrack poses of tracked quad
        """
        self.ego_est_pose.append(msg.pose)
        self.ego_est_time_pose.append(msg.header.stamp.to_sec())


    def parse_bb_msg(self, msg, t=None):
        """
        record times of detect
        note message is custom MSL-RAPTOR angled bounding box
        """
        # msg = msg.boxes[0]
        for msg in msg.boxes:
            name = msg.class_str
            t = msg.header.stamp.to_sec()
            # if msg.im_seg_mode == self.DETECT:
            #     self.detect_time[name].append(t)

            self.abb_list[name].append(([msg.x, msg.y, msg.width, msg.height, msg.angle*180./np.pi], msg.im_seg_mode))
            self.abb_time_list[name].append(t)
            ######
            # eps = 0.1 # min width of line
            # if msg.im_seg_mode == self.DETECT:  # we are detecting now
            #     if not self.detect_times[name]:  # first run - init list
            #         self.detect_times[name] = [[t]]
            #         self.detect_end[name] = np.nan
            #     elif len(self.detect_times[name][-1]) == 2: # we are starting a new run
            #         self.detect_times[name].append([t])
            #         self.detect_end[name] = np.nan
            #     else: # len(self.detect_times[-1]) = 1: # we are currently still on a streak of detects
            #         self.detect_end[name] = t
            # else: # not detecting
            #     if not self.detect_times[name] or len(self.detect_times[name][-1]) == 2: # we are still not detecting (we were not Detecting previously)
            #         pass
            #     else: # self.detect_times[-1][1]: # we were just tracking
            #         self.detect_times[name][-1].append(self.detect_end[name])
            #         self.detect_end[name] = np.nan

            # self.detect_mode[name].append(msg.im_seg_mode)


if __name__ == '__main__':
    try:
        if len(sys.argv) == 1:
            raise RuntimeError("not enough arguments, must pass in the rosbag name (w/ or w/o .bag)")
        elif len(sys.argv) == 2:
            my_rb_name = sys.argv[1]
            my_data_source = "raptor"
        elif len(sys.argv) == 3:
            my_rb_name = sys.argv[1]
            my_data_source = sys.argv[2]
        elif len(sys.argv) > 3:
            raise RuntimeError("too many arguments, only pass in the rosbag name (w/ or w/o .bag)")
        np.set_printoptions(linewidth=160, suppress=True)  # format numpy so printing matrices is more clear
        program = rosbags_to_logs(rb_name=my_rb_name, data_source=my_data_source)
        
    except:
        import traceback
        traceback.print_exc()
