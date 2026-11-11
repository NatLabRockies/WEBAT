"""
Manual Corner Detection and Validation Tool
Allows you to manually verify and correct checkerboard corner detection
"""

import cv2
import numpy as np
import json
import os
from pathlib import Path


class ManualCornerValidator:
    """
    Interactive tool for manual checkerboard corner detection and validation
    """
    
    def __init__(self, checkerboard_size=(9, 6)):
        """
        Initialize validator
        
        Args:
            checkerboard_size: (width, height) of checkerboard pattern
        """
        self.checkerboard_size = checkerboard_size
        self.corners_data = {}
        self.criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        
    def auto_detect_corners(self, image_path):
        """
        Attempt automatic corner detection
        
        Returns:
            tuple: (corners, image, success)
        """
        img = cv2.imread(image_path)
        if img is None:
            return None, None, False
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ret, corners = cv2.findChessboardCorners(gray, self.checkerboard_size, None)
        
        if ret:
            corners_refined = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), self.criteria)
            return corners_refined, img, True
        else:
            return None, img, False
    
    def _get_corner_color(self, index, total_corners):
        """
        Get color for corner based on position (gradient from blue to red)
        
        Args:
            index: Corner index (0 to total_corners-1)
            total_corners: Total number of corners
            
        Returns:
            tuple: (B, G, R) color
        """
        ratio = index / max(1, total_corners - 1)
        # Gradient: Blue (0) -> Cyan -> Green -> Yellow -> Red (1)
        if ratio < 0.25:
            # Blue to Cyan
            g = int(255 * (ratio / 0.25))
            return (255, g, 0)
        elif ratio < 0.5:
            # Cyan to Green
            b = int(255 * (1 - (ratio - 0.25) / 0.25))
            return (b, 255, 0)
        elif ratio < 0.75:
            # Green to Yellow
            r = int(255 * ((ratio - 0.5) / 0.25))
            return (0, 255, r)
        else:
            # Yellow to Red
            g = int(255 * (1 - (ratio - 0.75) / 0.25))
            return (0, g, 255)
    
    def visualize_detected_corners(self, image, corners, title="Detected Corners"):
        """
        Display image with detected corners overlaid, colorized by order
        
        Args:
            image: Input image
            corners: Detected corners or None
            title: Window title
        """
        display = image.copy()
        
        if corners is not None:
            total_corners = len(corners)
            
            # Draw corners with gradient coloring
            for i, corner in enumerate(corners):
                x, y = int(corner[0][0]), int(corner[0][1])
                color = self._get_corner_color(i, total_corners)
                
                # Draw filled circle with corner color only
                cv2.circle(display, (x, y), 5, color, -1)
            
            # Draw lines connecting corners in order
            w, h = self.checkerboard_size
            corners_reshaped = corners.reshape((h, w, 2))
            
            # Horizontal lines
            for row in range(h):
                for col in range(w - 1):
                    idx1 = row * w + col
                    pt1 = tuple(map(int, corners_reshaped[row, col]))
                    pt2 = tuple(map(int, corners_reshaped[row, col + 1]))
                    color = self._get_corner_color(idx1, total_corners)
                    cv2.line(display, pt1, pt2, color, 1)
            
            # Vertical lines
            for col in range(w):
                for row in range(h - 1):
                    idx1 = row * w + col
                    pt1 = tuple(map(int, corners_reshaped[row, col]))
                    pt2 = tuple(map(int, corners_reshaped[row + 1, col]))
                    color = self._get_corner_color(idx1, total_corners)
                    cv2.line(display, pt1, pt2, color, 1)
        
        cv2.imshow(title, display)
    
    def compare_detections(self, image_path_left, image_path_right, 
                          corners_left, corners_right):
        """
        Display side-by-side comparison of detections in left and right images
        with colorized corners to show order
        
        Args:
            image_path_left: Path to left image
            image_path_right: Path to right image
            corners_left: Detected corners in left image
            corners_right: Detected corners in right image
        """
        img_left = cv2.imread(image_path_left)
        img_right = cv2.imread(image_path_right)
        
        if img_left is None or img_right is None:
            print("❌ Could not load images")
            return
        
        # Visualize both with colorization
        display_left = img_left.copy()
        display_right = img_right.copy()
        
        total_left = len(corners_left) if corners_left is not None else 0
        total_right = len(corners_right) if corners_right is not None else 0
        
        # Draw left corners with gradient coloring
        if corners_left is not None:
            for i, corner in enumerate(corners_left):
                x, y = int(corner[0][0]), int(corner[0][1])
                color = self._get_corner_color(i, total_left)
                cv2.circle(display_left, (x, y), 5, color, -1)
        
        # Draw right corners with gradient coloring
        if corners_right is not None:
            for i, corner in enumerate(corners_right):
                x, y = int(corner[0][0]), int(corner[0][1])
                color = self._get_corner_color(i, total_right)
                cv2.circle(display_right, (x, y), 5, color, -1)
        
        # Resize to same height for side-by-side display
        h = max(display_left.shape[0], display_right.shape[0])
        w_left = int(display_left.shape[1] * h / display_left.shape[0])
        w_right = int(display_right.shape[1] * h / display_right.shape[0])
        display_left = cv2.resize(display_left, (w_left, h))
        display_right = cv2.resize(display_right, (w_right, h))
        
        combined = np.hstack([display_left, display_right])
        
        cv2.imshow("Comparison: LEFT vs RIGHT (Colorized by Corner Order)", combined)
    
    def validate_and_save_corners(self, image_dir, output_dir):
        """
        Validate corners in all image pairs and save accepted detections
        
        Args:
            image_dir: Directory with paired camera images
            output_dir: Directory to save validated corners
        """
        os.makedirs(output_dir, exist_ok=True)
        
        left_images = sorted([f for f in os.listdir(image_dir) 
                            if f.endswith(('.png')) and f.startswith("left_")])
        right_images = sorted([f for f in os.listdir(image_dir) 
                             if f.endswith(('.png')) and f.startswith("middle_")])
        
        validated_pairs = []
        
        print(f"\n{'='*70}")
        print("MANUAL CORNER VALIDATION")
        print(f"{'='*70}")
        print(f"Found {len(left_images)} left images and {len(right_images)} right images\n")
        
        for pair_idx, (left_file, right_file) in enumerate(zip(left_images, right_images), 1):
            left_path = os.path.join(image_dir, left_file)
            right_path = os.path.join(image_dir, right_file)
            
            print(f"\n[Pair {pair_idx}/{len(left_images)}]")
            print(f"  Left:  {left_file}")
            print(f"  Right: {right_file}")
            
            # Auto-detect
            corners_left, img_left, success_left = self.auto_detect_corners(left_path)
            corners_right, img_right, success_right = self.auto_detect_corners(right_path)
            
            if success_left and success_right:
                print(f"  ✅ Both auto-detected")
                
                # Show comparison
                self.compare_detections(left_path, right_path, corners_left, corners_right)
                
                # Ask user to validate
                print(f"\n  Press 'y' to ACCEPT, 'n' to REJECT, 'q' to QUIT (or any other key to see details)")
                key = cv2.waitKey(0)
                
                if key == ord('q'):
                    print(f"  Quitting...")
                    cv2.destroyAllWindows()
                    self.save_validation_report(validated_pairs, output_dir)
                    return validated_pairs
                elif key == ord('y'):
                    print(f"  ✅ Accepted")
                    validated_pairs.append({
                        'pair_num': pair_idx,
                        'left_file': left_file,
                        'right_file': right_file,
                        'left_corners': corners_left.tolist(),
                        'right_corners': corners_right.tolist(),
                        'validation': 'auto-detected'
                    })
                else:
                    print(f"  ❌ Rejected - Will need manual review or recapture")
                
                cv2.destroyAllWindows()
            
            elif success_left or success_right:
                print(f"  ⚠️  Only one image detected successfully")
                print(f"      Left: {'✅' if success_left else '❌'}")
                print(f"      Right: {'✅' if success_right else '❌'}")
                print(f"  ❌ Rejected - both cameras needed")
            
            else:
                print(f"  ❌ Neither image detected automatically")
                print(f"  Consider: Better lighting? Full checkerboard visible? Correct size?")
        
        # Save results
        self.save_validation_report(validated_pairs, output_dir)
        
        print(f"\n{'='*70}")
        print(f"VALIDATION COMPLETE")
        print(f"{'='*70}")
        print(f"✅ Valid pairs: {len(validated_pairs)}")
        print(f"❌ Invalid pairs: {len(left_images) - len(validated_pairs)}")
        print(f"Saved to: {output_dir}\n")
        
        return validated_pairs
    
    def save_validation_report(self, validated_pairs, output_dir):
        """
        Save validation report and corner data as JSON
        
        Args:
            validated_pairs: List of validated corner pairs
            output_dir: Output directory
        """
        report = {
            'checkerboard_size': self.checkerboard_size,
            'total_pairs': len(validated_pairs),
            'validated_pairs': validated_pairs
        }
        
        report_file = os.path.join(output_dir, 'validation_report.json')
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n📄 Validation report saved to: {report_file}")
        
        # Also create summary
        summary_file = os.path.join(output_dir, 'validation_summary.txt')
        with open(summary_file, 'w') as f:
            f.write("CORNER VALIDATION SUMMARY\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Checkerboard Size: {self.checkerboard_size}\n")
            f.write(f"Total Pairs Validated: {len(validated_pairs)}\n\n")
            f.write("Valid Pairs:\n")
            for pair in validated_pairs:
                f.write(f"  - Pair {pair['pair_num']}: {pair['left_file']}\n")
        
        print(f"📊 Summary saved to: {summary_file}")
    
    def export_for_calibration(self, validated_pairs, output_file):
        """
        Export validated corners in format suitable for calibration
        
        Args:
            validated_pairs: List of validated corner pairs
            output_file: Output JSON file
        """
        calibration_ready = {
            'checkerboard_size': self.checkerboard_size,
            'num_pairs': len(validated_pairs),
            'pairs': []
        }
        
        for pair in validated_pairs:
            calibration_ready['pairs'].append({
                'pair_num': pair['pair_num'],
                'left_corners': pair['left_corners'],
                'right_corners': pair['right_corners']
            })
        
        with open(output_file, 'w') as f:
            json.dump(calibration_ready, f)
        
        print(f"✅ Exported {len(validated_pairs)} pairs for calibration")
        print(f"   File: {output_file}")


def main():
    """
    Main validation workflow
    """
    print("\n" + "="*70)
    print("MANUAL CORNER VALIDATION TOOL")
    print("="*70)
    
    # Configuration
    IMAGES_DIR = "camera_calibration/experiment_data/frames_pair/"
    OUTPUT_DIR = "camera_calibration/experiment_data/validated_corners"
    CHECKERBOARD_SIZE = (7, 7)  # Change to your size
    
    # Initialize validator
    validator = ManualCornerValidator(checkerboard_size=CHECKERBOARD_SIZE)
    
    # Run validation
    validated_pairs = validator.validate_and_save_corners(
        IMAGES_DIR, 
        OUTPUT_DIR
    )
    
    # Export for calibration
    if validated_pairs:
        export_file = os.path.join(OUTPUT_DIR, 'validated_corners.json')
        validator.export_for_calibration(validated_pairs, export_file)
    
    print("Done!")


if __name__ == "__main__":
    main()
