import ssl

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.parameter import Parameter

from models.torchvision_backbones import TVDeeplabRes101Encoder

ssl._create_default_https_context = ssl._create_unverified_context


class FewShotSeg(nn.Module):
    def __init__(
        self,
        use_coco_init=True,
    ):
        super().__init__()

        # Encoder
        self.encoder = TVDeeplabRes101Encoder(use_coco_init)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.t = Parameter(torch.Tensor([-10.0]))
        self.scaler = 20.0
        self.criterion = nn.NLLLoss()
        self.self_attention = SelfAttention(256)
        self.cross_attention = CrossAttention(256)
        self.high_avg_pool = nn.AdaptiveAvgPool1d(256)
        self.conv_fusion = nn.Conv2d(256 + 1, 256, kernel_size=1)

    def generate_prior(self, query_feat, supp_feat, s_y, fts_size):
        bsize, _, sp_sz, _ = query_feat.size()[:]
        cosine_eps = 1e-7

        tmp_mask = (s_y == 1).float().unsqueeze(1)
        tmp_mask = F.interpolate(
            tmp_mask,
            size=(fts_size[0], fts_size[1]),
            mode="bilinear",
            align_corners=True,
        )

        tmp_supp_feat = supp_feat * tmp_mask
        q = self.high_avg_pool(
            query_feat.flatten(2).transpose(-2, -1)
        )  # [bs, h*w, 256]
        s = self.high_avg_pool(
            tmp_supp_feat.flatten(2).transpose(-2, -1)
        )  # [bs, h*w, 256]

        tmp_query = q
        tmp_query = tmp_query.contiguous().permute(0, 2, 1)  # [bs, 256, h*w]
        tmp_query_norm = torch.norm(tmp_query, 2, 1, True)

        tmp_supp = s
        tmp_supp = tmp_supp.contiguous()
        tmp_supp = tmp_supp.contiguous()
        tmp_supp_norm = torch.norm(tmp_supp, 2, 2, True)

        similarity = torch.bmm(tmp_supp, tmp_query) / (
            torch.bmm(tmp_supp_norm, tmp_query_norm) + cosine_eps
        )
        similarity = similarity.max(1)[0].view(bsize, sp_sz * sp_sz)
        similarity = (similarity - similarity.min(1)[0].unsqueeze(1)) / (
            similarity.max(1)[0].unsqueeze(1)
            - similarity.min(1)[0].unsqueeze(1)
            + cosine_eps
        )
        corr_query = similarity.view(bsize, 1, sp_sz, sp_sz)
        corr_query = F.interpolate(
            corr_query,
            size=(fts_size[0], fts_size[1]),
            mode="bilinear",
            align_corners=True,
        )
        corr_query_mask = corr_query.unsqueeze(1)
        return corr_query_mask

    def forward(
        self,
        supp_imgs,
        fore_mask,
        qry_imgs,
        train=False,
        t_loss_scaler=1,
        n_cmat=1,
        n_iters=1,
    ):
        """
        Args:
            supp_imgs: support images
                way x shot x [B x 3 x H x W], list of lists of tensors
            fore_mask: foreground masks for support images
                way x shot x [B x H x W], list of lists of tensors
            back_mask: background masks for support images
                way x shot x [B x H x W], list of lists of tensors
            qry_imgs: query images
                N x [B x 3 x H x W], list of tensors
        """

        n_ways = len(supp_imgs)
        self.n_shots = len(supp_imgs[0])
        self.n_ways = len(supp_imgs)
        self.n_shots = len(supp_imgs[0])
        self.n_queries = len(qry_imgs)
        assert (
            self.n_ways == 1
        )  # for now only one-way, because not every shot has multiple sub-images
        assert self.n_queries == 1
        n_queries = len(qry_imgs)
        self.batch_size_q = qry_imgs[0].shape[0]
        self.batch_size = supp_imgs[0][0].shape[0]
        img_size = supp_imgs[0][0].shape[-2:]

        # ###### Extract features ######
        imgs_concat = torch.cat(
            [torch.cat(way, dim=0) for way in supp_imgs]
            + [
                torch.cat(qry_imgs, dim=0),
            ],
            dim=0,
        )
        img_fts = self.encoder(imgs_concat, low_level=False)

        fts_size = img_fts.shape[-2:]
        supp_fts = img_fts[: n_ways * self.n_shots * self.batch_size].view(
            n_ways, self.n_shots, self.batch_size, -1, *fts_size
        )  # Wa x Sh x B x C x H' x W'
        qry_fts = img_fts[n_ways * self.n_shots * self.batch_size :].view(
            n_queries, self.batch_size_q, -1, *fts_size
        )  # N x B x C x H' x W'

        align_loss = torch.zeros(1).to(self.device)
        for _ in range(n_cmat):
            supp_fts, qry_fts, query_mask, align_loss2 = self.CMAT(
                supp_fts,
                fore_mask,
                qry_fts,
                # query_mask,
                img_size,
                fts_size,
                train,
                n_iters,
            )
            align_loss += align_loss2
        align_loss /= n_cmat

        return (
            query_mask,
            align_loss / self.batch_size,
        )

    def CMAT(
        self,
        supp_fts,
        fore_mask,
        qry_fts,
        # query_mask,
        img_size,
        fts_size,
        train,
        n_iters,
    ):
        # Reshape for self_attention
        supp_fts_reshaped = supp_fts.view(
            -1, *supp_fts.shape[-3:]
        )  # (Wa*Sh*B) x C x H' x W'
        qry_fts_reshaped = qry_fts.view(-1, *qry_fts.shape[-3:])  # (N*B) x C x H' x W'

        # Self attention
        supp_fts_reshaped = self.self_attention(supp_fts_reshaped)
        qry_fts_reshaped = self.self_attention(qry_fts_reshaped)

        # Reshape back to original size
        supp_fts = supp_fts_reshaped.view(
            self.n_ways, self.n_shots, self.batch_size, -1, *fts_size
        )  # Wa x Sh x B x C x H' x W'
        qry_fts = qry_fts_reshaped.view(
            self.n_queries, self.batch_size_q, -1, *fts_size
        )  # N x B x C x H' x W'

        fore_mask = torch.stack(
            [torch.stack(way, dim=0) for way in fore_mask], dim=0
        )  # Wa x Sh x B x H' x W'

        # ###### Generate prior ######
        qry_fts1 = qry_fts.view(
            -1, qry_fts.shape[2], *fts_size
        )  # (N * B) x C x H' x W'
        supp_fts1 = supp_fts.view(self.batch_size, -1, *fts_size)  # B x C x H' x W'
        fore_mask1 = fore_mask[0][0]  # B x H' x W'
        corr_query_mask = self.generate_prior(qry_fts1, supp_fts1, fore_mask1, (32, 32))

        # Reshape corr_query_mask from (N * B) x 1 x H' x W' to N x B x 1 x H' x W'
        query_mask = corr_query_mask.view(
            self.n_queries, self.batch_size_q, 1, *fts_size
        )
        # Fusion prior and query features
        qry_fts = torch.cat([qry_fts, query_mask], dim=2)  # N x B x (C + 1) x H' x W'
        qry_fts = self.conv_fusion(qry_fts.view(-1, qry_fts.shape[2], *fts_size)).view(
            self.n_queries, self.batch_size_q, -1, *fts_size
        )

        supp_fts_reshaped = supp_fts.view(-1, *supp_fts.shape[3:])
        qry_fts_reshaped = qry_fts.view(-1, *qry_fts.shape[2:])

        # Pass through CrossAttention
        supp_fts_out, qry_fts_out = self.cross_attention(
            supp_fts_reshaped, qry_fts_reshaped, fore_mask1, query_mask[0][0]
        )

        # Reshape back to original shape
        supp_fts = supp_fts_out.view(*supp_fts.shape)
        qry_fts = qry_fts_out.view(*qry_fts.shape)

        ###### Compute loss ######
        align_loss = torch.zeros(1).to(self.device)
        outputs = []
        for epi in range(self.batch_size):
            ###### Extract prototypes ######
            supp_fts_ = [
                [
                    self.getFeatures(
                        supp_fts[way, shot, [epi]], fore_mask[way, shot, [epi]]
                    )
                    for shot in range(self.n_shots)
                ]
                for way in range(self.n_ways)
            ]

            fg_prototypes = self.getPrototype(supp_fts_)
            anom_s = [
                self.negSim(qry_fts[epi], prototype) for prototype in fg_prototypes
            ]

            ###### Get threshold #######
            self.thresh_pred = [self.t for _ in range(self.n_ways)]
            self.t_loss = self.t / self.scaler

            ###### Get predictions #######
            pred = self.getPred(anom_s, self.thresh_pred)  # N x Wa x H' x W'

            qry_fts1 = [qry_fts]
            fg_prototypes1 = [fg_prototypes]
            qry_prediction = [
                torch.stack(
                    [
                        self.getPrediction(
                            qry_fts1[n][epi],
                            fg_prototypes1[n][way],
                            self.thresh_pred[way],
                        )
                        for way in range(self.n_ways)
                    ],
                    dim=1,
                )
                for n in range(len(qry_fts1))
            ]  # N x Wa x H' x W'

            ###### Prototype Refinement  ######
            fg_prototypes_ = []
            if (not train) and n_iters > 0:  # iteratively update prototypes
                for n in range(len(qry_fts1)):
                    fg_prototypes_.append(
                        self.updatePrototype(
                            qry_fts1[n],
                            fg_prototypes1[n],
                            qry_prediction[n],
                            n_iters,
                            epi,
                        )
                    )

                qry_prediction = [
                    torch.stack(
                        [
                            self.getPrediction(
                                qry_fts1[n][epi],
                                fg_prototypes_[n][way],
                                self.thresh_pred[way],
                            )
                            for way in range(self.n_ways)
                        ],
                        dim=1,
                    )
                    for n in range(len(qry_fts1))
                ]  # N x Wa x H' x W'

            pred_ups = [
                F.interpolate(
                    qry_prediction[n],
                    size=img_size,
                    mode="bilinear",
                    align_corners=True,
                )
                for n in range(len(qry_fts1))
            ]

            pred_ups = F.interpolate(
                pred, size=img_size, mode="bilinear", align_corners=True
            )
            pred_ups = torch.cat((1.0 - pred_ups, pred_ups), dim=1)

            outputs.append(pred_ups)

            ###### Prototype alignment loss ######
            if train:
                align_loss_epi = self.alignLoss(
                    qry_fts[:, epi],
                    torch.cat((1.0 - pred, pred), dim=1),
                    supp_fts[:, :, epi],
                    fore_mask[:, :, epi],
                )
                align_loss += align_loss_epi

        output = torch.stack(outputs, dim=1)  # N x B x (1 + Wa) x H x W
        output = output.view(-1, *output.shape[2:])

        return supp_fts, qry_fts, output, align_loss

    def updatePrototype(self, fts, prototype, pred, update_iters, epi):
        prototype_ = Parameter(torch.stack(prototype, dim=0))

        optimizer = torch.optim.Adam([prototype_], lr=0.01)

        while update_iters > 0:
            with torch.enable_grad():
                pred_mask = torch.sum(pred, dim=-3)
                pred_mask = torch.stack((1.0 - pred_mask, pred_mask), dim=1).argmax(
                    dim=1, keepdim=True
                )
                pred_mask = pred_mask.repeat([*fts.shape[1:-2], 1, 1])
                bg_fts = fts[epi] * (1 - pred_mask)
                fg_fts = torch.zeros_like(fts[epi])
                for way in range(self.n_ways):
                    fg_fts += (
                        prototype_[way].unsqueeze(-1).unsqueeze(-1).repeat(*pred.shape)
                        * pred_mask[way][None, ...]
                    )
                new_fts = bg_fts + fg_fts
                fts_norm = torch.sigmoid(
                    (fts[epi] - fts[epi].min()) / (fts[epi].max() - fts[epi].min())
                )
                new_fts_norm = torch.sigmoid(
                    (new_fts - new_fts.min()) / (new_fts.max() - new_fts.min())
                )
                bce_loss = nn.BCELoss()
                loss = bce_loss(fts_norm, new_fts_norm)

            optimizer.zero_grad()
            # loss.requires_grad_()
            loss.backward()
            optimizer.step()

            pred = torch.stack(
                [
                    self.getPrediction(fts[epi], prototype_[way], self.thresh_pred[way])
                    for way in range(self.n_ways)
                ],
                dim=1,
            )  # N x Wa x H' x W'

            update_iters += -1

        return prototype_

    def negSim(self, fts, prototype):
        """
        Calculate the distance between features and prototypes

        Args:
            fts: input features
                expect shape: N x C x H x W
            prototype: prototype of one semantic class
                expect shape: 1 x C
        """

        sim = -F.cosine_similarity(fts, prototype[..., None, None], dim=1) * self.scaler

        return sim

    def getFeatures(self, fts, mask):
        """
        Extract foreground and background features via masked average pooling

        Args:
            fts: input features, expect shape: 1 x C x H' x W'
            mask: binary mask, expect shape: 1 x H x W
        """

        fts = F.interpolate(fts, size=mask.shape[-2:], mode="bilinear")

        # masked fg features
        masked_fts = torch.sum(fts * mask[None, ...], dim=(2, 3)) / (
            mask[None, ...].sum(dim=(2, 3)) + 1e-5
        )  # 1 x C

        return masked_fts

    def getPrototype(self, fg_fts):
        """
        Average the features to obtain the prototype

        Args:
            fg_fts: lists of list of foreground features for each way/shot
                expect shape: Wa x Sh x [1 x C]
            bg_fts: lists of list of background features for each way/shot
                expect shape: Wa x Sh x [1 x C]
        """

        n_ways, n_shots = len(fg_fts), len(fg_fts[0])
        fg_prototypes = [
            torch.sum(torch.cat([tr for tr in way], dim=0), dim=0, keepdim=True)
            / n_shots
            for way in fg_fts
        ]  ## concat all fg_fts

        return fg_prototypes

    def alignLoss(self, qry_fts, pred, supp_fts, fore_mask):
        n_ways, n_shots = len(fore_mask), len(fore_mask[0])

        # Mask and get query prototype
        pred_mask = pred.argmax(dim=1, keepdim=True)  # N x 1 x H' x W'
        binary_masks = [pred_mask == i for i in range(1 + n_ways)]
        skip_ways = [i for i in range(n_ways) if binary_masks[i + 1].sum() == 0]
        pred_mask = torch.stack(
            binary_masks, dim=1
        ).float()  # N x (1 + Wa) x 1 x H' x W'
        qry_prototypes = torch.sum(qry_fts.unsqueeze(1) * pred_mask, dim=(0, 3, 4))
        qry_prototypes = qry_prototypes / (
            pred_mask.sum((0, 3, 4)) + 1e-5
        )  # (1 + Wa) x C

        # Compute the support loss
        loss = torch.zeros(1).to(self.device)
        for way in range(n_ways):
            if way in skip_ways:
                continue
            # Get the query prototypes
            for shot in range(n_shots):
                img_fts = supp_fts[way, [shot]]
                supp_sim = self.negSim(img_fts, qry_prototypes[[way + 1]])

                pred = self.getPred(
                    [supp_sim], [self.thresh_pred[way]]
                )  # N x Wa x H' x W'
                pred_ups = F.interpolate(
                    pred, size=fore_mask.shape[-2:], mode="bilinear", align_corners=True
                )
                pred_ups = torch.cat((1.0 - pred_ups, pred_ups), dim=1)

                # Construct the support Ground-Truth segmentation
                supp_label = torch.full_like(
                    fore_mask[way, shot], 255, device=img_fts.device
                )
                supp_label[fore_mask[way, shot] == 1] = 1
                supp_label[fore_mask[way, shot] == 0] = 0

                # Compute Loss
                eps = torch.finfo(torch.float32).eps
                log_prob = torch.log(torch.clamp(pred_ups, eps, 1 - eps))
                loss += (
                    self.criterion(log_prob, supp_label[None, ...].long())
                    / n_shots
                    / n_ways
                )

        return loss

    def getPred(self, sim, thresh):
        pred = []

        for s, t in zip(sim, thresh):
            pred.append(1.0 - torch.sigmoid(0.5 * (s - t)))

        return torch.stack(pred, dim=1)  # N x Wa x H' x W'

    def getPrediction(self, fts, prototype, thresh):
        """
        Calculate the distance between features and prototypes

        Args:
            fts: input features
                expect shape: N x C x H x W
            prototype: prototype of one semantic class
                expect shape: 1 x C
        """

        sim = -F.cosine_similarity(fts, prototype[..., None, None], dim=1) * self.scaler
        pred = 1.0 - torch.sigmoid(0.5 * (sim - thresh))

        return pred


class SelfAttention(nn.Module):
    def __init__(self, dim):
        super(SelfAttention, self).__init__()
        self.query = nn.Conv2d(dim, dim // 8, 1)
        self.key = nn.Conv2d(dim, dim // 8, 1)
        self.value = nn.Conv2d(dim, dim, 1)
        self.softmax = nn.Softmax(dim=-2)
        self.mlp = nn.Sequential(nn.Linear(dim, dim), nn.ReLU(), nn.Linear(dim, dim))
        self.norm = nn.LayerNorm([256, 32, 32])

    def forward(self, x):
        B, C, H, W = x.shape
        scale = (C // 8) ** -0.5
        q = self.query(x).view(B, -1, H * W).permute(0, 2, 1) * scale  # B, H*W, C'
        k = self.key(x).view(B, -1, H * W)  # B, C', H*W
        v = self.value(x).view(B, -1, H * W)  # B, C, H*W
        attn = self.softmax(torch.bmm(q, k))  # B, H*W, H*W
        out = torch.bmm(v, attn.permute(0, 2, 1)).view(B, C, H, W)  # B, C, H, W
        out = self.mlp(out.permute(0, 2, 3, 1)).permute(0, 3, 1, 2)
        out = out + x
        return self.norm(out)


class CrossAttention(nn.Module):
    def __init__(self, dim):
        super(CrossAttention, self).__init__()
        self.query = nn.Conv2d(dim, dim // 8, 1)
        self.key = nn.Conv2d(dim, dim // 8, 1)
        self.value = nn.Conv2d(dim, dim, 1)
        self.softmax = nn.Softmax(dim=-1)
        self.mlp = nn.Sequential(nn.Linear(dim, dim), nn.ReLU(), nn.Linear(dim, dim))
        self.norm = nn.LayerNorm([256, 32, 32])

    def forward(self, x, y, s_mask=None, q_mask=None):
        B, C, H, W = x.shape
        scale = (C // 8) ** -0.5

        qx = self.query(x).view(B, -1, H * W).permute(0, 2, 1) * scale  # B, H*W, C'
        ky = self.key(y).view(B, -1, H * W)  # B, C', H*W
        vy = self.value(y).view(B, -1, H * W)  # B, C, H*W
        attn = self.softmax(torch.bmm(qx, ky))  # B, H*W, H*W
        outx = torch.bmm(vy, attn.permute(0, 2, 1)).view(B, C, H, W)  # B, C, H, W

        if s_mask is not None:
            mask = s_mask.unsqueeze(0)
            mask = F.interpolate(
                mask,
                size=(H, W),
                mode="bilinear",
                align_corners=True,
            )
            outx = outx * mask

        outx = x + outx
        outx = self.norm(outx)  # Apply normalization

        outx2 = self.mlp(outx.permute(0, 2, 3, 1)).permute(
            0, 3, 1, 2
        )  # Apply MLP and permute back
        outx = outx + outx2
        outx = self.norm(outx)  # Apply normalization

        qy = self.query(y).view(B, -1, H * W).permute(0, 2, 1) * scale  # B, H*W, C'
        kx = self.key(x).view(B, -1, H * W)  # B, C', H*W
        vx = self.value(x).view(B, -1, H * W)  # B, C, H*W
        attn = self.softmax(torch.bmm(qy, kx))  # B, H*W, H*W
        outy = torch.bmm(vx, attn.permute(0, 2, 1)).view(B, C, H, W)  # B, C, H, W

        if q_mask is not None:
            mask = q_mask.unsqueeze(0)
            mask = F.interpolate(
                mask,
                size=(H, W),
                mode="bilinear",
                align_corners=True,
            )
            outy = outy * mask

        outy = y + outy
        outy = self.norm(outy)  # Apply normalization

        outy2 = self.mlp(outy.permute(0, 2, 3, 1)).permute(
            0, 3, 1, 2
        )  # Apply MLP and permute back
        outy = outy + outy2
        outy = self.norm(outy)  # Apply normalization

        return outx, outy
