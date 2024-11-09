# Training Cross Attention Transformer on the AMOS Dataset

## Highlights
<p align="justify">
We train and evaluate the Cross Masked Attention Transformer (CAT-Net) on the AMOS dataset. The CAT-Net mines the correlations between the support image and query image, limiting them to focus only on useful foreground information and boosting the representation capacity of both the support prototype and query features. For full details about the model, refer to the original paper:

> [Few Shot Medical Image Segmentation with Cross Attention Transformer](https://arxiv.org/abs/2303.13867) <br>
> Yi Lin*, Yufan Chen*, Kwang-Ting Cheng, Hao Chen


### Using the code
The code was cloned from (https://github.com/hust-linyi/CAT-Net) and was updated as the authors did not share the full working code of their method implementation. Please clone the repo:
```
git clone https://github.com/karabomaila/CAT-Net.git
```

### Requirements
1. create a virtual environment: ```python3 -m venv .venv``` and activate it ```. .venv/bin/activate```
2. ```pip install -r requirements.txt```

### Data preparation
#### Download
1. **Abdominal CT**  [Amos: A large-scale abdominal multi-organ benchmark for versatile medical image segmentation](https://zenodo.org/records/7262581)  

#### Pre-processing
The pre-processing code was taken from [Ouyang et al.](https://github.com/cheng-01037/Self-supervised-Fewshot-Medical-Image-Segmentation.git). The code was placed in the 'utils' folder.
1. Run the code in the `intensity_normalization.ipynb` file to normalize the images.
2. Run the code in the `resampling_and_roi.ipynb` file to fix the image boundary and resize the images.

### Training
1. Update the configurations in the 'train_amos.sh' and 'config.py' files.
2. Run the following command to train the model:
```
./exps/train_amos.sh
```

### Testing
Run `./exp/validation.sh`

## Acknowledgment 
This code is based on [CAT-Net](https://github.com/hust-linyi/CAT-Net) and [Ouyang et al.](https://github.com/cheng-01037/Self-supervised-Fewshot-Medical-Image-Segmentation.git), thanks for their excellent work!
