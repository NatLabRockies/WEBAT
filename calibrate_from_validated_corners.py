"""
Stereo Camera Calibration using Manually Validated Corners
Uses pre-validated corner positions from manual_corner_validator.py
"""

import cv2
import numpy as np
import os
import json
from pathlib import Path


class StereoCalibrationFromValidatedCorners:
    """
    Calibrate stereo camera using manually validated corner data
    """
    
    def __init__(self, checkerboard_size=(9, 6), square_size_mm=30):
        """
        Initialize calibrator
        
        Args:
            checkerboard_size: (width, height) of inner corners
            square_size_mm: Physical size of one square in mm
        """
        self.checkerboard_size = checkerboard_size
        self.square_size_mm = square_size_mm
        self.criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        
        # Prepare object points
        self.objp = np.zeros((checkerboard_size[0] * checkerboard_size[1], 3), np.float32)
        self.objp[:, :2] = np.mgrid[0:checkerboard_size[0], 0:checkerboard_size[1]].T.reshape(-1, 2)
        self.objp *= square_size_mm
        
        self.objpoints = []
        self.imgpoints_left = []
        self.imgpoints_right = []
    
    def load_validated_corners(self, validation_file):
        """
        Load validated corners from JSON file
        
        Args:
            validation_file: Path to validation_report.json from manual_corner_validator.py
            
        Returns:
            list: Validated corner pairs
        """
        print(f"\n📂 Loading validated corners from: {validation_file}")
        
        with open(validation_file, 'r') as f:
            data = json.load(f)
        
        validated_pairs = data.get('validated_pairs', [])
        
        print(f"✅ Loaded {len(validated_pairs)} validated pairs")
        
        return validated_pairs
    
    def prepare_calibration_data(self, validated_pairs, image_width=640, image_height=480):
        """
        Prepare calibration data from validated corners
        
        Args:
            validated_pairs: List of validated corner pairs
            image_width: Width of calibration images
            image_height: Height of calibration images
            
        Returns:
            tuple: (objpoints, imgpoints_left, imgpoints_right, img_shape)
        """
        objpoints = []
        imgpoints_left = []
        imgpoints_right = []
        
        for pair in validated_pairs:
            # Add object points
            objpoints.append(self.objp)
            
            # Convert corner lists to numpy arrays
            corners_left = np.array(pair['left_corners'], dtype=np.float32)
            corners_right = np.array(pair['right_corners'], dtype=np.float32)
            
            imgpoints_left.append(corners_left)
            imgpoints_right.append(corners_right)
        
        img_shape = (image_width, image_height)
        
        print(f"\n📊 Prepared calibration data:")
        print(f"   Object points: {len(objpoints)}")
        print(f"   Left image points: {len(imgpoints_left)}")
        print(f"   Right image points: {len(imgpoints_right)}")
        print(f"   Image shape: {img_shape}")
        
        return objpoints, imgpoints_left, imgpoints_right, img_shape
    
    def calibrate(self, objpoints, imgpoints_left, imgpoints_right, img_shape):
        """
        Perform stereo calibration
        
        Args:
            objpoints: 3D world coordinates
            imgpoints_left: 2D image coordinates for left camera
            imgpoints_right: 2D image coordinates for right camera
            img_shape: (width, height) of images
            
        Returns:
            dict: Calibration results
        """
        print(f"\n🔄 Calibrating stereo cameras...")
        
        # Calibrate individual cameras
        print("   Calibrating left camera...")
        ret_left, mtx_left, dist_left, rvecs_left, tvecs_left = cv2.calibrateCamera(
            objpoints, imgpoints_left, img_shape, None, None
        )
        print(f"      RMS: {ret_left:.4f}")
        
        print("   Calibrating right camera...")
        ret_right, mtx_right, dist_right, rvecs_right, tvecs_right = cv2.calibrateCamera(
            objpoints, imgpoints_right, img_shape, None, None
        )
        print(f"      RMS: {ret_right:.4f}")
        
        # Stereo calibration
        print("   Performing stereo calibration...")
        flags = cv2.CALIB_FIX_INTRINSIC
        ret_stereo, mtx_left, dist_left, mtx_right, dist_right, R, T, E, F = cv2.stereoCalibrate(
            objpoints, imgpoints_left, imgpoints_right,
            mtx_left, dist_left, mtx_right, dist_right, img_shape,
            criteria=self.criteria, flags=flags
        )
        print(f"      Stereo RMS: {ret_stereo:.4f}")
        
        # Stereo rectification
        print("   Computing stereo rectification...")
        R1, R2, P1, P2, Q, validRoi1, validRoi2 = cv2.stereoRectify(
            mtx_left, dist_left, mtx_right, dist_right, img_shape, R, T, alpha=-1
        )
        
        # Generate rectification maps
        print("   Generating rectification maps...")
        map1_left, map2_left = cv2.initUndistortRectifyMap(
            mtx_left, dist_left, R1, P1, img_shape, cv2.CV_16SC2
        )
        map1_right, map2_right = cv2.initUndistortRectifyMap(
            mtx_right, dist_right, R2, P2, img_shape, cv2.CV_16SC2
        )
        
        result = {
            'mtx_left': mtx_left,
            'dist_left': dist_left,
            'mtx_right': mtx_right,
            'dist_right': dist_right,
            'R': R,
            'T': T,
            'R1': R1,
            'R2': R2,
            'P1': P1,
            'P2': P2,
            'Q': Q,
            'map1_left': map1_left,
            'map2_left': map2_left,
            'map1_right': map1_right,
            'map2_right': map2_right,
            'img_shape': img_shape,
            'rms_error': ret_stereo
        }
        
        return result
    
    def save_calibration_xml(self, calib_data, output_dir, filename='stereoMap_validated.xml'):
        """
        Save calibration to XML file
        
        Args:
            calib_data: Calibration results from calibrate()
            output_dir: Output directory
            filename: Output filename
        """
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, filename)
        
        cv_file = cv2.FileStorage(output_file, cv2.FILE_STORAGE_WRITE)
        
        # Save maps as float32
        map1_left = calib_data['map1_left']
        map1_right = calib_data['map1_right']
        
        cv_file.write('stereoMapL_x', map1_left[:, :, 0].astype(np.float32))
        cv_file.write('stereoMapL_y', map1_left[:, :, 1].astype(np.float32))
        cv_file.write('stereoMapR_x', map1_right[:, :, 0].astype(np.float32))
        cv_file.write('stereoMapR_y', map1_right[:, :, 1].astype(np.float32))
        cv_file.write('Q', calib_data['Q'])
        
        # Save camera matrices
        cv_file.write('K_left', calib_data['mtx_left'])
        cv_file.write('K_right', calib_data['mtx_right'])
        cv_file.write('D_left', calib_data['dist_left'])
        cv_file.write('D_right', calib_data['dist_right'])
        cv_file.write('R', calib_data['R'])
        cv_file.write('T', calib_data['T'])
        
        cv_file.release()
        
        print(f"\n✅ Saved calibration to: {output_file}")
        print(f"   RMS Error: {calib_data['rms_error']:.4f}")
        print(f"   Image shape: {calib_data['img_shape']}")


def main():
    """
    Main calibration workflow using validated corners
    """
    print("\n" + "="*70)
    print("STEREO CALIBRATION FROM VALIDATED CORNERS")
    print("="*70)
    
    # Configuration
    VALIDATION_FILE = "camera_calibration/experiment_data/validated_corners/validation_report.json"
    OUTPUT_DIR = "./camera_calibration"
    CHECKERBOARD_SIZE = (7, 7)
    SQUARE_SIZE_MM = 100
    IMAGE_WIDTH = 640
    IMAGE_HEIGHT = 480
    
    # Initialize calibrator
    calibrator = StereoCalibrationFromValidatedCorners(
        checkerboard_size=CHECKERBOARD_SIZE,
        square_size_mm=SQUARE_SIZE_MM
    )
    
    # Load validated corners
    try:
        validated_pairs = calibrator.load_validated_corners(VALIDATION_FILE)
    except FileNotFoundError:
        print(f"❌ Validation file not found: {VALIDATION_FILE}")
        print(f"Run manual_corner_validator.py first!")
        return
    
    if not validated_pairs:
        print("❌ No validated pairs found!")
        return
    
    # Prepare calibration data
    objpoints, imgpoints_left, imgpoints_right, img_shape = calibrator.prepare_calibration_data(
        validated_pairs,
        image_width=IMAGE_WIDTH,
        image_height=IMAGE_HEIGHT
    )
    
    # Perform calibration
    calib_data = calibrator.calibrate(
        objpoints, imgpoints_left, imgpoints_right, img_shape
    )
    
    # Save results
    calibrator.save_calibration_xml(calib_data, OUTPUT_DIR, 'stereoMap_validated.xml')
    
    print(f"\n{'='*70}")
    print("✅ CALIBRATION COMPLETE")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
