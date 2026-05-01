'''
This script is to test 3D thermal bat tracking system

How to Run:
(tf_gpu02) python3.9 test_depth_analysis_with_two.py --middle ../results/Middle\ 2023-07-06_18_59_59_952/Middle\ 2023-07-06_18_59_59_952_bat.csv --right ../results/Far_Right\ 2023-07-06_19_00_00_259/Far_Right\ 2023-07-06_19_00_00_259_bat.csv

Input: Left and Right csv files to analyze
Output: 3D bat/bird/insect tracking graph after false positive removal
Elapsed Time in seconds: 1486.657817363739, 233.14813995361328
'''
import os, sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPT_DIR))

from webat.model.depth_analysis import *
from webat.utility.utils import set_directory, read_csv_file
import pandas as pd
import argparse
import time

def main():

    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--middle", help="path to the middle video file")
    ap.add_argument("-r", "--right", help="path to the right video file")
    args = vars(ap.parse_args())
    middle_filepath = args["middle"]
    right_filepath = args["right"]

    startTime = time.time()             # Record the elapsed time for the process

    # Read dataframes (2D Bat Tracking Output from each video)
    df_middle = pd.read_csv(middle_filepath)
    df_right = pd.read_csv(right_filepath)
    middle_file, separator, extension = middle_filepath.split("/")[-1].partition('.')
    right_file, separator, extension = right_filepath.split("/")[-1].partition('.')
    print("Filenames: ", middle_file, right_file)               # e.g.) Far_Left 2023-07-06_18_59_59_820_bat Middle 2023-07-06_18_59_59_952_bat Far_Right 2023-07-06_19_00_00_259_bat

    assert middle_file[-3:] == right_file[-3:]        # ends with 'bat' or same object type
    assert middle_file.split(" ")[-1].split("_")[0] == right_file.split(" ")[-1].split("_")[0]        # same dates
    date = middle_file.split(" ")[-1].split("_")[0]

    # Estimate the frame drift
    frame_diff_mr = calculate_frame_drift(30.0, middle_file, right_file)
    print("frame difference (right-middle): ", frame_diff_mr)

    # Path for saving results
    new_filename = date + '_' + middle_file.split(' ')[-1].split('_')[-1]
    save_dir = '../depth_analysis/'+new_filename
    set_directory('../depth_analysis')
    set_directory(save_dir)
    os.chdir(save_dir)

    # Dataframe to save the results
    df_bats = create_df_analysis()
    
    # Middle & Far_Right => Left is standard
    df_bats = match_frames_mr(df_middle, df_right, frame_diff_mr, df_bats)
    
    df_bats_sorted = df_bats.sort_values(by=['Object ID', 'Middle Image'], key=lambda x: x if not '_' in x else x.str.split("_", expand=True)[9].str[:-4].astype(int))
    df_bats_sorted.to_excel(f'../Full_bat_{new_filename}.xlsx')
    endTime = time.time() 
    executionTime = endTime - startTime
    print('Execution time in seconds: ' + str(executionTime))


if __name__ == "__main__":
    main()