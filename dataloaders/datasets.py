"""
Dataset for Training and Test
Extended from ADNet code by Hansen et al.
"""

import glob
import os
import random

import numpy as np
import SimpleITK as sitk
import torch
import torchvision.transforms as deftfx
from torch.utils.data import Dataset

from utils import resize_image_scipy

from . import image_transforms as myit
from .dataset_specifics import *


class TestDataset(Dataset):
    def __init__(self, args):
        # reading the paths
        if args["dataset"] == "CMR":
            self.image_dirs = glob.glob(
                os.path.join(args["data_dir"], "cmr_MR_normalized/image*")
            )
        elif args["dataset"] == "CHAOST2":
            self.image_dirs = glob.glob(
                os.path.join(args["data_dir"], "normalized/image*")
            )
        elif args["dataset"] == "SABS":
            self.image_dirs = glob.glob(
                os.path.join(args["data_dir"], "sabs_CT_normalized/image*")
            )
        elif args["dataset"] == "AMOS":
            self.image_dirs = glob.glob(
                os.path.join(args["data_dir"], "amos_CT_normalized/image*")
            )
            self.label_dirs = glob.glob(
                os.path.join(args["data_dir"], "amos_CT_normalized/label*")
            )

        self.image_dirs = sorted(
            self.image_dirs, key=lambda x: int(x.split("_")[-1].split(".nii.gz")[0])
        )

        # remove test fold!
        self.FOLD = get_folds(args["dataset"])
        self.image_dirs = [
            elem
            for idx, elem in enumerate(self.image_dirs)
            if idx in self.FOLD[args["eval_fold"]]
        ]

        # split into support/query
        idx = np.arange(len(self.image_dirs))
        self.support_dir = self.image_dirs[idx[args["supp_idx"]]]
        self.image_dirs.pop(idx[args["supp_idx"]])  # remove support
        self.label = None

    def __len__(self):
        return len(self.image_dirs)

    def __getitem__(self, idx):
        img_path = self.image_dirs[idx]
        img = sitk.GetArrayFromImage(sitk.ReadImage(img_path))
        new_shape = [31, 256, 256]
        img = resize_image_scipy(img, new_shape)

        img = (img - img.mean()) / img.std()
        img = np.stack(3 * [img], axis=1)

        lbl = sitk.GetArrayFromImage(
            sitk.ReadImage(
                img_path.split("image_")[0] + "label_" + img_path.split("image_")[-1]
            )
        )
        lbl = resize_image_scipy(lbl, new_shape)
        lbl[lbl == 200] = 1
        lbl[lbl == 500] = 2
        lbl[lbl == 600] = 3
        lbl = 1 * (lbl == self.label)

        sample = {"id": img_path}

        # Evaluation protocol.
        idx = lbl.sum(axis=(1, 2)) > 0
        sample["image"] = torch.from_numpy(img[idx])
        sample["label"] = torch.from_numpy(lbl[idx])
        return sample

    def get_support_index(self, n_shot, C):
        """
        Selecting intervals according to Ouyang et al.
        """
        if n_shot == 1:
            pcts = [0.5]
        else:
            half_part = 1 / (n_shot * 2)
            part_interval = (1.0 - 1.0 / n_shot) / (n_shot - 1)
            pcts = [half_part + part_interval * ii for ii in range(n_shot)]

        return (np.array(pcts) * C).astype("int")

    def getSupport(self, label=None, all_slices=True, N=None):
        if label is None:
            raise ValueError("Need to specify label class!")

        img_path = self.support_dir
        img = sitk.GetArrayFromImage(sitk.ReadImage(img_path))
        new_shape = [31, 256, 256]
        img = resize_image_scipy(img, new_shape)

        img = (img - img.mean()) / img.std()
        img = np.stack(3 * [img], axis=1)

        lbl = sitk.GetArrayFromImage(
            sitk.ReadImage(
                img_path.split("image_")[0] + "label_" + img_path.split("image_")[-1]
            )
        )
        lbl = resize_image_scipy(lbl, new_shape)
        lbl[lbl == 200] = 1
        lbl[lbl == 500] = 2
        lbl[lbl == 600] = 3
        lbl = 1 * (lbl == label)

        sample = {}
        if all_slices:
            sample["image"] = torch.from_numpy(img)
            sample["label"] = torch.from_numpy(lbl)
        else:
            # select N labeled slices
            if N is None:
                raise ValueError("Need to specify number of labeled slices!")
            idx = lbl.sum(axis=(1, 2)) > 0
            idx_ = self.get_support_index(N, idx.sum())

            sample["image"] = torch.from_numpy(img[idx][idx_])
            sample["label"] = torch.from_numpy(lbl[idx][idx_])

        return sample


class TrainDataset(Dataset):
    def __init__(self, args):
        self.n_shot = args["n_shot"]
        self.n_way = args["n_way"]
        self.n_query = args["n_query"]
        self.n_sv = args["n_sv"]
        self.max_iter = args["max_iter"]
        self.read = True  # read images before get_item
        self.train_sampling = "neighbors"
        self.min_size = args["min_size"]
        self.test_label = args["test_label"]
        self.exclude_label = args["exclude_label"]
        self.use_gt = args["use_gt"]

        # reading the paths (leaving the reading of images into memory to __getitem__)
        if args["dataset"] == "CMR":
            self.image_dirs = glob.glob(
                os.path.join(args["data_dir"], "cmr_MR_normalized/image*")
            )
            self.label_dirs = glob.glob(
                os.path.join(args["data_dir"], "cmr_MR_normalized/label*")
            )
        elif args["dataset"] == "CHAOST2":
            self.image_dirs = glob.glob(
                os.path.join(args["data_dir"], "normalized/image*")
            )
            self.label_dirs = glob.glob(
                os.path.join(args["data_dir"], "normalized/label*")
            )
        elif args["dataset"] == "SABS":
            self.image_dirs = glob.glob(
                os.path.join(args["data_dir"], "sabs_CT_normalized/image*")
            )
            self.label_dirs = glob.glob(
                os.path.join(args["data_dir"], "sabs_CT_normalized/label*")
            )

        elif args["dataset"] == "AMOS":
            self.image_dirs = glob.glob(
                os.path.join(args["data_dir"], "amos_CT_normalized/image*")
            )
            self.label_dirs = glob.glob(
                os.path.join(args["data_dir"], "amos_CT_normalized/label*")
            )

        self.image_dirs = sorted(
            self.image_dirs, key=lambda x: int(x.split("_")[-1].split(".nii.gz")[0])
        )
        self.label_dirs = sorted(
            self.label_dirs, key=lambda x: int(x.split("_")[-1].split(".nii.gz")[0])
        )
        self.sprvxl_dirs = glob.glob(
            os.path.join(args["data_dir"], "supervoxels_" + str(args["n_sv"]), "super*")
        )
        self.sprvxl_dirs = sorted(
            self.sprvxl_dirs, key=lambda x: int(x.split("_")[-1].split(".nii.gz")[0])
        )

        # remove test fold!
        self.FOLD = get_folds(args["dataset"])
        self.image_dirs = [
            elem
            for idx, elem in enumerate(self.image_dirs)
            if idx not in self.FOLD[args["eval_fold"]]
        ]
        self.label_dirs = [
            elem
            for idx, elem in enumerate(self.label_dirs)
            if idx not in self.FOLD[args["eval_fold"]]
        ]
        self.sprvxl_dirs = [
            elem
            for idx, elem in enumerate(self.sprvxl_dirs)
            if idx not in self.FOLD[args["eval_fold"]]
        ]

        # read images
        if self.read:
            self.images = {}
            self.labels = {}
            self.sprvxls = {}
            for image_dir, label_dir in zip(self.image_dirs, self.label_dirs):
                img = sitk.GetArrayFromImage(
                    sitk.ReadImage(image_dir)
                )
                gt  = sitk.GetArrayFromImage(
                    sitk.ReadImage(label_dir)
                )
                new_shape = [img.shape[0], 256, 256]
                self.images[image_dir] = resize_image_scipy(img, new_shape)
                self.labels[label_dir] = resize_image_scipy(gt, new_shape)

    def __len__(self):
        return self.max_iter

    def gamma_tansform(self, img):
        gamma_range = (0.5, 1.5)
        gamma = np.random.rand() * (gamma_range[1] - gamma_range[0]) + gamma_range[0]
        cmin = img.min()
        irange = img.max() - cmin + 1e-5

        img = img - cmin + 1e-5
        img = irange * np.power(img * 1.0 / irange, gamma)
        img = img + cmin

        return img

    def geom_transform(self, img, mask):
        affine = {"rotate": 5, "shift": (5, 5), "shear": 5, "scale": (0.9, 1.2)}
        alpha = 10
        sigma = 5
        order = 3

        tfx = []
        tfx.append(
            myit.RandomAffine(
                affine.get("rotate"),
                affine.get("shift"),
                affine.get("shear"),
                affine.get("scale"),
                affine.get("scale_iso", True),
                order=order,
            )
        )
        tfx.append(myit.ElasticTransform(alpha, sigma))
        transform = deftfx.Compose(tfx)

        if len(img.shape) > 4:
            n_shot = img.shape[1]
            for shot in range(n_shot):
                cat = np.concatenate((img[0, shot], mask[:, shot])).transpose(1, 2, 0)
                cat = transform(cat).transpose(2, 0, 1)
                img[0, shot] = cat[:3, :, :]
                mask[:, shot] = np.rint(cat[3:, :, :])

        else:
            for q in range(img.shape[0]):
                cat = np.concatenate((img[q], mask[q][None])).transpose(1, 2, 0)
                cat = transform(cat).transpose(2, 0, 1)
                img[q] = cat[:3, :, :]
                mask[q] = np.rint(cat[3:, :, :].squeeze())

        return img, mask

    def __getitem__(self, idx):
        # sample patient idx
        pat_idx = random.choice(range(len(self.image_dirs)))

        if self.read:
            # get image/supervoxel volume from dictionary
            img = self.images[self.image_dirs[pat_idx]]
            gt = self.labels[self.label_dirs[pat_idx]]
        else:
            # read image/supervoxel volume into memory
            img = sitk.GetArrayFromImage(sitk.ReadImage(self.image_dirs[pat_idx]))
            gt = sitk.GetArrayFromImage(sitk.ReadImage(self.label_dirs[pat_idx]))

            new_shape = [img.shape[0], 256, 256]
            img = resize_image_scipy(img, new_shape)
            gt = resize_image_scipy(gt, new_shape)

        exclude_idx = []

        # normalize
        img = (img - img.mean()) / img.std()

        # chose training label
        if self.use_gt:
            lbl = gt.copy()

        # sample class(es) (gt/supervoxel)
        unique = list(np.unique(lbl))
        for i in range(len(unique)):
            unique[i] = int(unique[i])
        unique.remove(0)
        if self.use_gt:
            unique = list(set(unique) - set(self.test_label))
            unique = list(set(unique) - set(self.exclude_label))

        size = 0
        while size < self.min_size:
            n_slices = (self.n_shot * self.n_way) + self.n_query - 1
            while n_slices < ((self.n_shot * self.n_way) + self.n_query):
                cls_idx = random.choice(unique)

                # extract slices containing the sampled class
                sli_idx = np.sum(lbl == cls_idx, axis=(1, 2)) > 0
                idx = np.arange(lbl.shape[0])
                sli_idx = idx[sli_idx]

                sli_idx = list(
                    set(sli_idx) - set(np.intersect1d(sli_idx, exclude_idx))
                )  # remove slices containing test labels
                n_slices = len(sli_idx)

            # generate possible subsets with successive slices (size = self.n_shot * self.n_way + self.n_query)
            subsets = []
            for i in range(len(sli_idx)):
                if not subsets:
                    subsets.append([sli_idx[i]])
                elif sli_idx[i - 1] + 1 == sli_idx[i]:
                    subsets[-1].append(sli_idx[i])
                else:
                    subsets.append([sli_idx[i]])
            i = 0
            while i < len(subsets):
                if len(subsets[i]) < (self.n_shot * self.n_way + self.n_query):
                    del subsets[i]
                else:
                    i += 1
            if not len(subsets):
                return self.__getitem__(
                    idx + np.random.randint(low=0, high=self.max_iter - 1, size=(1,))
                )

            # sample support and query slices
            i = random.choice(np.arange(len(subsets)))  # subset index
            i = random.choice(
                subsets[i][: -(self.n_shot * self.n_way + self.n_query - 1)]
            )
            sample = np.arange(i, i + (self.n_shot * self.n_way) + self.n_query)

            lbl_cls = 1 * (lbl == cls_idx)

            size = max(np.sum(lbl_cls[sample[0]]), np.sum(lbl_cls[sample[1]]))

        # invert order
        if np.random.random(1) > 0.5:
            sample = sample[::-1]  # successive slices (inverted)

        sup_lbl = lbl_cls[sample[: self.n_shot * self.n_way]][
            None,
        ]  # n_way * (n_shot * C) * H * W
        qry_lbl = lbl_cls[sample[self.n_shot * self.n_way :]]  # n_qry * C * H * W

        sup_img = img[sample[: self.n_shot * self.n_way]][
            None,
        ]  # n_way * (n_shot * C) * H * W

        sup_img = np.stack((sup_img, sup_img, sup_img), axis=2)
        qry_img = img[sample[self.n_shot * self.n_way :]]  # n_qry * C * H * W
        # print(qry_img.shape)
        qry_img = np.stack((qry_img, qry_img, qry_img), axis=1)
        padding_mask = np.zeros_like(qry_lbl)
        s_padding_mask = np.zeros_like(sup_lbl)
        # gamma transform
        if np.random.random(1) > 0.5:
            qry_img = self.gamma_tansform(qry_img)
        else:
            sup_img = self.gamma_tansform(sup_img)

        # geom transform
        if np.random.random(1) > 0.5:
            qry_img, qry_lbl = self.geom_transform(qry_img, qry_lbl)
        else:
            (
                sup_img,
                sup_lbl,
            ) = self.geom_transform(sup_img, sup_lbl)

        sample = {
            "support_images": sup_img,
            "support_fg_labels": sup_lbl,
            "query_images": qry_img,
            "query_labels": qry_lbl,
            "padding_mask": padding_mask,
            "s_padding_mask": s_padding_mask,
        }
        return sample
