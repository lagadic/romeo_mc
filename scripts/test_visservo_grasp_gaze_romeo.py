#! /usr/bin/env python
import roslib; roslib.load_manifest('mc_control')
import rospy
import numpy as np
from eigen3 import Vector3d, Matrix3d, toEigenX, Vector2d, Quaterniond
import spacevecalg as sva
import rbdyn as rbd
import tasks
from mc_rbdyn import loadRobots, rbdList, MRContact
from mc_solver import MRQPSolver, DynamicsConstraint, ContactConstraint, \
  KinematicsConstraint, CollisionsConstraint, Collision
from joint_state_publisher import JointStatePublisher
from mc_robot_msgs.msg import MCRobotState
from ask_user import ask_user
from geometry_msgs.msg import PoseStamped, TransformStamped
from std_msgs.msg import Int8

import tf
from mc_ros_utils import transform

import sys
import copy

from naoqi import ALProxy
import time


# private helper scripts within the package
from helper_scripts.tasks_helper import orientationTask, \
  comTask, positionTask, pbvsTask
from helper_scripts.stop_experiment_helper import stopMotion, goHalfSitting
from helper_scripts.plot_task_error import TaskErrorPub
from helper_scripts.fake_vision import fakeVision

"""
This script is used to test a position-based visual servoing task in the
multi_contact framework as a QP task. An image-visual servoing controll is added such
that the gaze tracke the hand. An interactive object is used to define the goal.
"""

# control parameters
#timeStep = 0.005
timeStep = 0.01

if __name__ == '__main__':
  rospy.init_node('test_visservo_grasp_gaze_romeo')
  mode = sys.argv[1]
  print 'usage mode is ', mode
  if mode not in ['test','box_grasping']:
    rospy.logwarn('Invalid mode, defaulting to normal\
       \nusage: test_visservo_grasp_gaze_romeo.py mode\
       \nwhere mode can be: \
       \n test : PBVS hand using the desired pose of the hand published by visp_blob_tracker \
       \n box_grasping : PBVS hand using MBT to grasp and object' )

       # load the robot and the environment
  robots = loadRobots()
  for r in robots.robots:
    r.mbc.gravity = Vector3d(0., 0., 9.81)

  romeo_index = 0
  env_index = 1
 # romeo_real_index = 2

  romeo = robots.robots[romeo_index]
  env = robots.robots[env_index]
 # romeo_real = robots.robots[romeo_real_index]

  # compute foot position to be in contact with the ground
  for rob in [romeo]:
    rbd.forwardKinematics(rob.mb, rob.mbc)
    tz = -rob.surfaces['Lfoot'].X_0_s(rob).translation().z()
    tx = -rob.surfaces['Lfoot'].X_0_s(rob).translation().x() #zero the feet surface
    rob_q = rbdList(rob.mbc.q)

    rob_q[0] = [1., 0., 0., 0., tx, 0., tz]
    rob.mbc.q = rob_q
    if rob is romeo:
      romeo_q = rob_q

  # compute init fk and fv
  for r in robots.robots:
    rbd.forwardKinematics(r.mb, r.mbc)
    rbd.forwardVelocity(r.mb, r.mbc)

  romeoJsp = JointStatePublisher(romeo)
  #romeo_real_Jsp = JointStatePublisher(romeo_real)

  # create solver
  qpsolver = MRQPSolver(robots, timeStep)

  # add dynamics constraint to QPSolver
  # Use 50% of the velocity limits cf Sebastien Langagne.
  contactConstraint = ContactConstraint(timeStep, ContactConstraint.Position)
  dynamicsConstraint1 = DynamicsConstraint(robots, romeo_index, timeStep,
                                           damper=(0.1, 0.01, 0.5), velocityPercent=0.5)
  kinConstraint1 = KinematicsConstraint(robots, romeo_index, timeStep,
                                        damper=(0.1, 0.01, 0.5), velocityPercent=0.5)
  qpsolver.addConstraintSet(contactConstraint)
  qpsolver.addConstraintSet(dynamicsConstraint1)

  tfListener = tf.TransformListener()
  tfWriter = tf.TransformBroadcaster()

  # Self-collision robot
  cols = []
  # Collision between arms
  armsBodies = ['{prefL}_wrist', '{prefU}Elbow']
  for lab in map(lambda x: x.format(prefL='l', prefU='L'), armsBodies):
    for rab in map(lambda x: x.format(prefL='r', prefU='R'), armsBodies):
      cols.append(Collision(lab, rab, 0.10, 0.05, 0.))


  cols += [Collision('torso', 'HeadRoll_link', 0.055, 0.015, 0.),# 0.1, 0.02, 0.
           Collision('l_wrist', 'torso', 0.05, 0.01, 0.), 
           Collision('l_wrist', 'body', 0.05, 0.01, 0.),
           Collision('l_wrist', 'NeckPitch_link', 0.05, 0.01, 0.),
           Collision('l_wrist', 'HeadRoll_link', 0.05, 0.01, 0.),
           Collision('l_wrist', 'LThigh', 0.05, 0.01, 0.),
           Collision('l_wrist', 'LTibia', 0.05, 0.01, 0.),
           Collision('r_wrist', 'torso', 0.05, 0.01, 0.),
           Collision('r_wrist', 'body', 0.05, 0.01, 0.),
           Collision('r_wrist', 'NeckPitch_link', 0.05, 0.01, 0.),
           Collision('r_wrist', 'HeadRoll_link', 0.05, 0.01, 0.),
           Collision('r_wrist', 'RThigh', 0.05, 0.01, 0.),
           Collision('r_wrist', 'RTibia', 0.05, 0.01, 0.),
           Collision('LElbow', 'torso', 0.05, 0.01, 0.),
           Collision('LElbow', 'body', 0.05, 0.01, 0.),
           Collision('LElbow', 'NeckPitch_link', 0.05, 0.01, 0.),
           Collision('LElbow', 'HeadRoll_link', 0.05, 0.01, 0.),
           Collision('RElbow', 'torso', 0.05, 0.01, 0.),
           Collision('RElbow', 'body', 0.05, 0.01, 0.),
           Collision('RElbow', 'NeckPitch_link', 0.05, 0.01, 0.),
           Collision('RElbow', 'HeadRoll_link', 0.05, 0.01, 0.),
           # Collision('RThigh', 'LThigh', 0.01, 0.001, 0.),
           # Collision('RTibia', 'LTibia', 0.05, 0.01, 0.),
           # Collision('r_ankle', 'l_ankle', 0.05, 0.01, 0.),
           # Collision('RThigh', 'LTibia', 0.05, 0.01, 0.),
           # Collision('LThigh', 'RTibia', 0.05, 0.01, 0.),
           # Collision('r_ankle', 'LTibia', 0.05, 0.01, 0.),
           # Collision('l_ankle', 'RTibia', 0.05, 0.01, 0.),
           # Collision('r_ankle', 'LThigh', 0.05, 0.01, 0.),
           # Collision('l_ankle', 'RThigh', 0.05, 0.01, 0.),
          ]

  r1SelfCollisionConstraint = CollisionsConstraint(robots, romeo_index,
                                                   romeo_index, timeStep)
  r1SelfCollisionConstraint.addCollisions(robots, cols)


  qpsolver.addConstraintSet(r1SelfCollisionConstraint)

  # Useful robot surfaces
  rFoot = romeo.surfaces['Rfoot']
  lFoot = romeo.surfaces['Lfoot']
  rHand = romeo.surfaces['Rhand']
  lHand = romeo.surfaces['Lhand']

  # Initial target goals (arbitrary)
  rf_pos_goal = rFoot.X_0_s(romeo).translation()
  rf_ori_goal = rFoot.X_0_s(romeo).rotation()
  rh_pos_goal = rHand.X_0_s(romeo).translation()# + Vector3d(0.1,  0.1, 0.0) #+ Vector3d(0.1, -0.1, 0.3)
  lh_ori_goal = lHand.X_0_s(romeo).rotation()
  lh_pos_goal = lHand.X_0_s(romeo).translation()# + Vector3d(0.1,  0.1, 0.3)
  hand_rotation_goal = rHand.X_0_s(romeo).rotation()
  #lHand.X_0_s(romeo).rotation() #sva.RotY(-np.pi/2.)

  # Setting up the tasks
  postureTask1 = tasks.qp.PostureTask(robots.mbs, romeo_index, romeo_q, 0.1, 10.) #0.1,  15.

  # rfPosTask, rfPosTaskSp = positionTask(robots, romeo_index, 'r_ankle',
  #                                       rf_pos_goal,
  #                                       5., 1000., rFoot.X_b_s.translation())
  # rfOriTask, rfOriTaskSp = orientationTask(robots, romeo_index, 'r_ankle', rf_ori_goal,
  #                                          5., 100.)
  
  rhOriTask, rhOriTaskSp = orientationTask(robots, romeo_index, 'r_wrist', hand_rotation_goal,
                                           2., 100.)
  torsoOriTask, torsoOriTaskSp = orientationTask(robots, romeo_index, 'torso',
                                                 Matrix3d.Identity(), 1., 15.)  #2., 15.
  headOriTask, headOriTaskSp = orientationTask(robots, romeo_index, 'HeadRoll_link',
                                                 list(romeo.mbc.bodyPosW)[romeo.bodyIndexByName('HeadRoll_link')].rotation(), 10., 10.)
  # comTask, comTaskSp = comTask(robots, romeo_index, rbd.computeCoM(romeo.mb, romeo.mbc),
  #                              5., 100000.)

  # print "COM:" , rbd.computeCoM(romeo.mb, romeo.mbc) # : -0.0482481,-2.00112e-09, 0.694905

  comTask, comTaskSp = comTask(robots, romeo_index, Vector3d(-0.02, 0, rbd.computeCoM(romeo.mb, romeo.mbc)[2]),
                               5., 100000.)
  # Set up tasks 
  # Transformation from r_wrist to hand target
  trans = (0.033096434903, 0.0486815138012, 0.0318448350088)
  quat = (0.577328318474, 0.0275670521388, 0.110292994864, 0.80855891907)
  offset_X_b_s = transform.fromTf(trans, quat)

  rhPbvsTask, rhPbvsTaskSp = pbvsTask(robots, romeo_index, 'r_wrist',
                                     sva.PTransformd.Identity(),
                                     1.5, 2500., offset_X_b_s) # 1000
  # rhPbvsTask, rhPbvsTaskSp = pbvsTask(robots, romeo_index, 'r_wrist',
  #                                    sva.PTransformd.Identity(),
  #                                    5., 1000.)

  rhPosTask, rhPosTaskSp = positionTask(robots, romeo_index, 'r_wrist',
                                        rh_pos_goal,
                                        1.5, 1000., rHand.X_b_s.translation())

  # Task for left arm: mantain pose and orientation
  # lhPosTask, lhPosTaskSp = positionTask(robots, romeo_index, 'l_wrist',
  #                                     lh_pos_goal,
  #                                     2., 1000., lHand.X_b_s.translation() )

  # lhOriTask, lhOriTaskSp = orientationTask(robots, romeo_index, 'l_wrist', lh_ori_goal,
  #                                          2., 100.)

  # A posture task for the left arm to lessen its flailing
  postureTaskLeftArm = tasks.qp.PostureTask(robots.mbs, romeo_index, romeo_q, 0.0, 10.)
  jointStiffness = []
  for j in romeo.mb.joints():
    if j.name() in [  'LShoulderPitch', 'LShoulderYaw', 'LElbowRoll', 'LElbowYaw', 'LWristRoll', 'LWristYaw', 'LWristPitch']:
      jointStiffness.append(tasks.qp.JointStiffness(j.id(), 2.))
  postureTaskLeftArm.jointsStiffness(robots.mbs, jointStiffness)

  task_error_to_pub = rhPbvsTask

  # disable the CoM height
  #com_axis_weight = np.mat([1., 1., 1e-3]).T
  com_axis_weight = np.mat([1., 1., 1e-4]).T
  comTaskSp.dimWeight(toEigenX(com_axis_weight))

  # add tasks to the solver
  qpsolver.solver.addTask(rhPosTaskSp)
  qpsolver.solver.addTask(rhOriTaskSp)

  # qpsolver.solver.addTask(lhPosTaskSp) 
  # qpsolver.solver.addTask(lhOriTaskSp)
  qpsolver.solver.addTask(postureTaskLeftArm) #TODO: add in only in the right places


  qpsolver.solver.addTask(torsoOriTaskSp)
  #qpsolver.solver.addTask(headOriTaskSp)
  qpsolver.solver.addTask(comTaskSp)
  qpsolver.solver.addTask(postureTask1)

  # setup all
  c1L = MRContact(romeo_index, env_index,
                  lFoot, env.surfaces['AllGround'])
  c1R = MRContact(romeo_index, env_index,
                  rFoot, env.surfaces['AllGround'])

  qpsolver.setContacts([c1L, c1R])
  qpsolver.update()

  class Controller(object):
    def __init__(self):
      self.isRunning = True
      self.stopCB = ask_user.askUserNonBlock('stop_control', 'Stop')
      self.graspCB = ask_user.askUserNonBlock('grasp', 'Grasp')

      # initialize fake Vision
      self.X_gaze_hand = sva.PTransformd(Vector3d.Zero()) #TODO: better init
      self.X_gaze_object = sva.PTransformd(Vector3d.Zero()) #TODO: better init
      # self.hand_frame = '/'+str(romeo_index)+'/r_wrist'
      self.hand_frame = '/'+str(romeo_index)+'/rhand_target1'
      self.object_frame = '/object/base_link'
      self.tf_base_frame = '/'+str(romeo_index)+'/CameraLeftEye_optical_frame'
      self.fake_vision = fakeVision(self.tf_base_frame)

      #  Hand Target pose 
      self.target_pose_hand = sva.PTransformd(Vector3d(0,0,1))
      #  Desired Hand Target pose 
      self.target_des_pose_hand = sva.PTransformd(Vector3d(0,0,1))      
      # Status tracker hand target
      self.status_tracker_hand = 0;
      #  Object pose
      self.X_c_box = sva.PTransformd(Vector3d(0,0,1)) 
      # Status tracker hand target
      self.status_tracker_box = 0;

      #  for plotting task error
      self.task_error_pub = TaskErrorPub(task_error_to_pub, 'rh_PBVS_task')

      # Transformation from the box to hand target desired pose for the grasping
      #trans = (-0.03765, 0.126563, 0.002561) 
      trans = (-0.07765, 0.126563, 0.002561) 
      quat = (-0.67277591331, 0.687752570778, 0.0465059942478, -0.268712047286)
      self.offset_X_box_hd = transform.fromTf(trans, quat)
      trans_grasp = (-0.03765, 0.126563, 0.002561) 
      self.offset_X_box_hd_grasp = transform.fromTf(trans_grasp, quat) 

      # gaze task to be created later
      self.gazeTaskLeft = []
      self.gazeTaskLeftTr = []
      self.gazeTaskRight = []
      self.gazeTaskRightTr = []

      # sequence of states - each must correspond to a method of this object
      self.fsm_sequence  = ['wait_init_position',
                            'init_gaze_task',
                            'visual_servo_grasp']
      self.checkSequence()

    # check if there are still states in the FSM to be executed
    def checkSequence(self):
      if self.fsm_sequence:
        print 'Sequence left: ', self.fsm_sequence
        self.fsm = getattr(self, self.fsm_sequence.pop(0))
      else:
        self.fsm = self.idle
        print 'idling'

    def wait_init_position(self, rs):
      # self.fakeControl()
      # self.addUncertainties()
      if rhPosTask.eval().norm() < 0.1 and rhPosTask.speed().norm() < 0.001:
        # remove the right hand positioning task and replace it with a position based visual servoing task
        qpsolver.solver.removeTask(rhPosTaskSp)
        qpsolver.solver.removeTask(rhOriTaskSp)
        qpsolver.solver.addTask(rhPbvsTaskSp)
        qpsolver.update()
        self.checkSequence()

    def init_gaze_task(self, rs):
      try:
        (trans, quat) = tfListener.lookupTransform('/0/LEye', '0/CameraLeftEye_optical_frame', rospy.Time(0))
        X_b_gaze = transform.fromTf(trans, quat)

        #target_pose_hand = self.fake_vision(self.hand_frame)
        # self.gazeTask = tasks.qp.GazeTask(robots.mbs, romeo_index,
        #                                 robots.robots[romeo_index].bodyIdByName('LEye'),
        #                                 self.target_pose_hand.translation(), X_b_gaze)
        # self.gazeTaskSp = tasks.qp.SetPointTask(robots.mbs, romeo_index, self.gazeTask, 1., 50.)
        #qpsolver.solver.addTask(self.gazeTaskSp)

        self.gazeTaskLeft = tasks.qp.GazeTask(robots.mbs, romeo_index,
                                        robots.robots[romeo_index].bodyIdByName('LEye'),
                                        self.target_pose_hand.translation(), X_b_gaze)
        gaze_task_gain = 4.
        damping_factor = 2.0 # must be greater than 1
        self.gazeTaskLeftTr = tasks.qp.TrajectoryTask(robots.mbs, romeo_index, self.gazeTaskLeft, gaze_task_gain, damping_factor*2*np.sqrt(gaze_task_gain), 60.) #  10., 50.
        qpsolver.solver.addTask(self.gazeTaskLeftTr)

        # Task for right eye
        # (transl, quatl) = tfListener.lookupTransform('/0/REye', '0/CameraRightEye_optical_frame', rospy.Time(0))
        # X_b_rgaze = transform.fromTf(transl, quatl)
        # self.gazeTaskRight = tasks.qp.GazeTask(robots.mbs, romeo_index,
        #                                 robots.robots[romeo_index].bodyIdByName('REye'),
        #                                 self.target_pose.translation(), X_b_rgaze)
        # self.gazeTaskRigthtTr = tasks.qp.TrajectoryTask(robots.mbs, romeo_index, self.gazeTaskRight, gaze_task_gain-5, damping_factor*2*np.sqrt(gaze_task_gain), 30.) #  10., 50.
        # qpsolver.solver.addTask(self.gazeTaskRigthtTr)

        # for plotting task error
        self.task_error_pub_gaze = TaskErrorPub(self.gazeTaskLeft, 'gaze_IBVS_task')

        print 'gaze task added'
        self.checkSequence()
      except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException):
        print 'tf exception'

    def visual_servo_grasp(self, rs):

      self.X_gaze_hand = self.fake_vision(self.hand_frame)
      self.X_gaze_object = self.fake_vision(self.object_frame)
      # update PBVS scheme with the pose of the real hand in the target frame

      if (self.status_tracker_hand == 1):
        if (mode == 'test' or mode == 'box_grasping' and self.status_tracker_box != 1):
          rhPbvsTask.error(self.target_pose_hand * self.target_des_pose_hand.inv()) # (X_c_h * X_c_dh.inv())
        elif (self.status_tracker_box == 1 and mode == 'box_grasping'):
          rhPbvsTask.error(self.target_pose_hand * self.X_c_box.inv() * self.offset_X_box_hd.inv() ) # (X_c_h * X_c_dh.inv())
          # rhPbvsTask.error(self.target_pose_hand * self.target_pose_hand.inv() )          
      else:
        rhPbvsTask.error(self.X_gaze_hand * self.X_gaze_object.inv())

      # if (self.status_tracker_hand == 1):
        # print "---------------------------------------------------"
        # print "Real error: "
        # error_ = self.target_pose_hand.inv() * self.target_des_pose_hand
        # print  "Translation:"
        # print error_.translation()
        # print  "Rotation:"
        # print error_.rotation()        
        # print "---------------------------------------------------"

      # print "---------------------------------------------------"
      # print "Virtual error: "
      # b = self.X_gaze_hand * self.X_gaze_object.inv()
      # print  "Translation:"
      # print b.translation()
      # print  "Rotation:"
      # print b.rotation()   
      # print "---------------------------------------------------"


      if (self.status_tracker_hand == 1):
        if (self.status_tracker_box ==1):
          self.gazeTaskLeft.error((self.target_pose_hand.translation() + self.X_c_box.translation())/2, Vector2d(0.0, -0.02)) #center the object in the image frame
        else:
          #self.gazeTaskLeft.error(self.target_pose_hand.translation(), Vector2d(0.1, 0.1)) #center the object in the image frame Vector2d(0.1, -0.03)
          self.gazeTaskLeft.error(self.X_gaze_object.translation(), Vector2d(0.2, 0.0)) # Vector2d(0.2, 0.07))
      else:
        self.gazeTaskLeft.error(self.X_gaze_object.translation(), Vector2d(0.2, 0.0)) # Vector2d(0.2, 0.07))
      #print 'eval: ', self.gazeTask.eval()

    # main control loop
    def run(self, rs):
      if self.graspCB is not None and self.graspCB.check():
        print 'grasping pose'
        self.graspCB = None
        self.isRunning = True
        self.offset_X_box_hd = self.offset_X_box_hd_grasp 

      if self.stopCB is not None and self.stopCB.check():
        print 'stopping'
        self.stopCB = None
        self.isRunning = True
        self.hsCB = stopMotion(robots, qpsolver, postureTask1, None, rbdList(romeo.mbc.q))
        try:
          PORT = 9559
          robotIP = "198.18.0.1"
          motionProxy = ALProxy("ALMotion", robotIP, PORT)
        except Exception,e:
          print "Could not create proxy to ALMotion"
          print "Error was: ",e
          sys.exit(1)

        # Close the hand 
        handName  = 'RHand'
        #motionProxy.closeHand(handName)
        motionProxy.setStiffnesses(handName, 1.0)
        angle  = 0.10
        fractionMaxSpeed  = 0.2
        motionProxy.setAngles(handName, angle, fractionMaxSpeed)
        time.sleep(1.0)

        # Motion to raise the box
        chainName = "RArm"
        frame     = 0 # TORSO
        useSensor = True
        # Get the current position of the chainName in the same frame
        current = motionProxy.getPosition(chainName, frame, useSensor)

        target = [
            current[0] + 0.00,
            current[1] + 0.00,
            current[2] + 0.09,
            current[3] + 0.0,
            current[4] + 0.0,
            current[5] + 0.0]

        fractionMaxSpeed = 0.3
        axisMask         = 7 # just control position
        motionProxy.setPositions(chainName, frame, target, fractionMaxSpeed, axisMask)
        time.sleep(1.0)

        self.fsm = self.waitHS

      if self.isRunning:
        if not qpsolver.run():
          print 'FAIL !!!'
          self.isRunning = False
          return
        curTime = rs.header.stamp

        romeoJsp.publish(curTime)
        #romeo_real_Jsp.publish(curTime)
        self.task_error_pub.publish()
        if self.fsm == self.visual_servo_grasp:
          self.task_error_pub_gaze.publish()

        qpsolver.send(curTime)

        self.fsm(rs)
#        if not (self.fsm == self.wait_init_position):
#          raw_input('wait user input')

    # FSM state: after stopped go back to half-sitting
    def waitHS(self, rs):
      if self.hsCB is not None and self.hsCB.check():
        self.hsCB = None
        goHalfSitting(qpsolver, postureTask1, romeo_q, \
                      [dynamicsConstraint1, contactConstraint], \
                      [kinConstraint1])
        self.fsm = self.idle
        print 'idling'


    def TargetPoseCB(self, pose):
      self.target_pose_hand = transform.fromPose(pose.pose) # X_c_h
      #print self.target_pose.translation()
      [tf_tran,tf_quat] = transform.toTf(self.target_pose_hand)
      tfWriter.sendTransform(tf_tran,
                        tf_quat,
                        rospy.Time.now(),
                        "/Target_hand",
                        "0/CameraLeftEye_optical_frame")

    def TargetDesPoseCB(self, pose):
      self.target_des_pose_hand = transform.fromPose(pose.pose) # X_c_dh
      #print self.target_pose.translation()
      [tf_tran,tf_quat] = transform.toTf(self.target_des_pose_hand)
      tfWriter.sendTransform(tf_tran,
                        tf_quat,
                        rospy.Time.now(),
                        "/Des_Target_hand",
                        "0/CameraLeftEye_optical_frame")

    def statusTrackerCB(self, status):
      self.status_tracker_hand = status.data

    def objectDesPoseCB(self, pose):
      self.X_c_box = transform.fromTransform(pose.transform) # X_c_box
      #print self.target_pose.translation()

    def statusMbtTrackerCB(self, status):
      self.status_tracker_box = status.data


    def idle(self, rs):
      pass

  ask_user.askUser('start', 'Start')
  controller = Controller()
  rospy.Subscriber('/robot/sensors/robot_state', MCRobotState,
                   controller.run, queue_size=10, tcp_nodelay=True)
  rospy.Subscriber('/visp_blobs_tracker/object_position', PoseStamped,
                   controller.TargetPoseCB, queue_size=10, tcp_nodelay=True)
  rospy.Subscriber('/visp_blobs_tracker/status', Int8,
                   controller.statusTrackerCB, queue_size=10, tcp_nodelay=True)
  rospy.Subscriber('/visp_blobs_tracker/object_des_position', PoseStamped,
                   controller.TargetDesPoseCB, queue_size=10, tcp_nodelay=True)

  if mode == 'box_grasping':
    rospy.Subscriber('/object_position', TransformStamped,
                   controller.objectDesPoseCB, queue_size=10, tcp_nodelay=True)
    rospy.Subscriber('/tracker_mbt/status', Int8,
                   controller.statusMbtTrackerCB, queue_size=10, tcp_nodelay=True)



  rospy.spin()
