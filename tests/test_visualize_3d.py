'''
This script is to visualize the bat tracking path as a 3D plot. Please note that this is not the 3D real world coordinates, but:
- x-axis: x-axis of the rectified frame from Middle camera
- y-axis: y-axis of the rectified frame from Middle camera
- z-axis: the height of bat

How to Run:
(tf_gpu02) python3.9 test_visualize_3d.py ../final_training_set/Full_bat_Middle\ 2023-07-06_18_59_59_952_bat_Far_Right\ 2023-07-06_19_00_00_259_bat.xlsx

Input: Bat/bird/insect tracking result after false positive removal
Output: 3D Visualization of flight track
'''
import os, sys
import re
import pandas as pd
import plotly.express as px
import plotly.offline as py

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    excel_file_path = sys.argv[1]
    filename = excel_file_path.split('/')[-1]
    obj_name = filename.split('_')[1]
    date = filename.split('_')[-1][:-5]

    df_org = pd.read_excel(excel_file_path)
    dct = {f'df{idx}': group for idx, group in df_org.groupby('Object ID')}
    print(dct)

    for key, df in dct.items():
        object_id = key[2:]
        df['X'] = df['Rectified Middle Center (px)'].apply(lambda x: int(re.split('\(|\)|, ', x)[1]))
        df['Y'] = df['Rectified Middle Center (px)'].apply(lambda x: int(re.split('\(|\)|, ', x)[2]))
        
        fig = px.scatter_3d(df, x='X', y='Y', z='Depth Prediction (cm)', animation_frame='Time')

        fig.update_layout(
            title = f"{date} {obj_name}",
            scene = dict(
                xaxis = dict(nticks=10, range=[0, 640]),
                yaxis = dict(nticks=10, range=[0, 480]),
                zaxis = dict(nticks=10, range=[0, 9000])
            ),
            scene_camera = dict(
                up = dict(x=0, y=0, z=1),
                eye = dict(x=0.1, y=-2.5, z=0.1)
            )
        )

        fig.write_html(f'{date}_{obj_name}{object_id}.html')
        # py.plot(fig, filename=f"{date}_{obj_name}.html", auto_open=True)
        # plt_div = plot(fig, output_type='div')
    

if __name__ == "__main__":
    main()