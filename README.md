# WEBAT
The WEBAT (Wind Energy with Bat AI-based Tracker) is a Python-based bat tracking software, integrating machine learning and computer vision with infrared thermal sensors to enhance the monitoring and protection of bats in proximity to wind turbines. This software supports extracting 2D (pixelwise coordinates) and 3D (real-world coordinates) flight trajectories of bat, bird, and insects.

![WEBAT Demo](figures/282.gif)

## Installation
<!-- TODO: Think about better installation. Not installing `git-lfs` separately. setup.py? -->

To download the full codes, including large-sized model, `git-lfs` needs to be installed first.

For MacOS:
```
brew install git-lfs
git lfs install                 # Update global git config
git lfs install --system        # Update system git config
```

For Windows:
```
git lfs install
```

For Ubuntu:
```
sudo apt install git-lfs
git lfs install
```

<!-- TODO: Let's give more options for set up (e.g., pip install, etc.) -->

Then, proceed `git clone` and set up the environment with `conda`.
```
git clone git@github.com:NatLabRockies/WEBAT.git
conda env create --name webat-env -f environment.yml
conda activate webat-env
```

Please additionally install cudatoolkit, cudnn, cuda if you wish to use GPU.


## Acknowledgements
Relased under software record NREL/SWR-24-121

### Developer Contacts
- Sora Ryu, National Renewable Energy Laboratory, sora.ryu@nlr.gov

### Recommended Citation
Ryu, Sora, Yarbrough, John, Rooney, Samantha, and Hein, Cris. WEBAT (Wind Energy with Bat AI-based Tracker) [SWR-24-121]. Computer Software. https://github.com/NREL/WEBAT. USDOE Office of Energy Efficiency and Renewable Energy (EERE), Renewable Power Office. Wind Energy Technologies Office. 06 Nov. 2024. Web. doi:10.11578/dc.20241119.3.
