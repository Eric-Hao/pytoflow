import os
import math
import numpy as np
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import torch.utils.serialization

UNKNOWN_FLOW_THRESH = 1e7
arguments_strModel = 'F'
SpyNet_model_dir = '/home/ftp/Coldog/DeepLearning/TOFlow/branch/models'  # SpyNet模型参数目录


class visualization():
    def __init__(self):
        self.colorwheel = self.create_colorwheel()

    def create_colorwheel(self):
        """
        Generate color wheel according Middlebury color code
        :return: Color wheel
        """
        RY, YG, GC, CB, BM, MR = (15, 6, 4, 11, 13, 6)
        ncols = RY + YG + GC + CB + BM + MR

        colorwheel = np.zeros((ncols, 3))
        col = 0

        colorwheel[0:RY, 0:1] = 255
        colorwheel[0:RY, 1] = np.linspace(0, 255, RY)
        col += RY

        colorwheel[col:col + YG, 0] = np.linspace(255, 0, YG)
        colorwheel[col:col + YG, 1:2] = 255
        col += YG

        colorwheel[col:col + GC, 1:2] = 255
        colorwheel[col:col + GC, 2] = np.linspace(0, 255, GC)
        col += GC

        colorwheel[col:col + CB, 1] = np.linspace(255, 0, CB)
        colorwheel[col:col + CB, 2:] = 255
        col += CB

        colorwheel[col:col + BM, 0] = np.linspace(0, 255, BM)
        colorwheel[col:col + BM, 2:] = 255
        col += BM

        colorwheel[col:col + MR, 0:1] = 255
        colorwheel[col:col + MR, 2] = np.linspace(255, 0, MR)
        col += MR

        return colorwheel

    def compute_color(self, u, v):
        """
        compute optical flow color map
        :param u: optical flow horizontal map
        :param v: optical flow vertical map
        :return: optical flow in color code
        """
        [h, w] = u.shape
        img = np.zeros([h, w, 3])
        nanIdx = np.isnan(u) | np.isnan(v)  # the where flows are nan.
        u[nanIdx] = 0
        v[nanIdx] = 0

        ncols = self.colorwheel.shape[0]

        rad = np.sqrt(u ** 2 + v ** 2)

        angle = np.arctan2(-v, -u) / np.pi

        fk = (angle + 1) / 2 * (ncols - 1) + 1

        k0 = np.floor(fk).astype(int)

        k1 = k0 + 1
        k1[k1 == ncols + 1] = 1
        f = fk - k0

        for i in range(0, self.colorwheel.shape[1]):
            tmp = self.colorwheel[:, i]
            col0 = tmp[k0 - 1] / 255
            col1 = tmp[k1 - 1] / 255
            col = (1 - f) * col0 + f * col1

            idx = rad <= 1
            col[idx] = 1 - rad[idx] * (1 - col[idx])
            notidx = np.logical_not(idx)

            col[notidx] *= 0.75
            img[:, :, i] = np.uint8(np.floor(255 * col * (1 - nanIdx)))
        return img

    # visualization
    def flow_to_image(self, flow):
        """
        Convert flow into middlebury color code image
        :param flow: optical flow map
        :return: optical flow image in middlebury color
        """
        # fx and fy optical flow
        u = flow[:, :, 0].cpu().detach().numpy()
        v = flow[:, :, 1].cpu().detach().numpy()

        # the where flows are too large.
        idxUnknow = (abs(u) > UNKNOWN_FLOW_THRESH) | (abs(v) > UNKNOWN_FLOW_THRESH)
        u[idxUnknow] = 0
        v[idxUnknow] = 0

        rad = np.sqrt(u ** 2 + v ** 2)
        maxrad = max(-1, np.max(rad))

        # print ("max flow: %.4f\nflow range:\nu = %.3f .. %.3f\nv = %.3f .. %.3f" % (maxrad, minu,maxu, minv, maxv))

        u = u / (maxrad + np.finfo(float).eps)
        v = v / (maxrad + np.finfo(float).eps)

        img = self.compute_color(u, v)

        idx = np.repeat(idxUnknow[:, :, np.newaxis], 3, axis=2)
        img[idx] = 0

        return np.uint8(img)


class SpyNet(torch.nn.Module):
    def __init__(self, cuda_flag):
        super(SpyNet, self).__init__()
        self.cuda_flag = cuda_flag

        class Preprocess(torch.nn.Module):
            def __init__(self):
                super(Preprocess, self).__init__()

            # end

            # RGB normalization, but I forgot why we should do so.
            # tensorInput (batch_size, n_channel, width, height)
            def forward(self, tensorInput):
                tensorBlue = (tensorInput[:, 0:1, :, :] - 0.406) / 0.225
                tensorGreen = (tensorInput[:, 1:2, :, :] - 0.456) / 0.224
                tensorRed = (tensorInput[:, 2:3, :, :] - 0.485) / 0.229

                return torch.cat([tensorRed, tensorGreen, tensorBlue], 1)
                # restore them as beginning after normalization. 标准化后拼回来原来的样子
            # end

        # end

        class Basic(torch.nn.Module):
            def __init__(self, intLevel):
                super(Basic, self).__init__()

                # Gk
                self.moduleBasic = torch.nn.Sequential(
                    # in_channels=8 是因为RGB*2+flow(3*2+2)=8  flow initialized with zeros.
                    torch.nn.Conv2d(in_channels=8, out_channels=32, kernel_size=7, stride=1, padding=3),
                    torch.nn.ReLU(inplace=False),
                    # inplace – can optionally do the operation in-place. Default: False
                    torch.nn.Conv2d(in_channels=32, out_channels=64, kernel_size=7, stride=1, padding=3),
                    torch.nn.ReLU(inplace=False),
                    torch.nn.Conv2d(in_channels=64, out_channels=32, kernel_size=7, stride=1, padding=3),
                    torch.nn.ReLU(inplace=False),
                    torch.nn.Conv2d(in_channels=32, out_channels=16, kernel_size=7, stride=1, padding=3),
                    torch.nn.ReLU(inplace=False),
                    torch.nn.Conv2d(in_channels=16, out_channels=2, kernel_size=7, stride=1, padding=3)
                )

                # load parameters into the Conv2d Layer
                for intConv in range(5):
                    self.moduleBasic[intConv * 2].weight.data.copy_(torch.utils.serialization.load_lua(
                        SpyNet_model_dir + '/modelL%d_%s-%d-weight.t7' % (
                            intLevel + 1, arguments_strModel, intConv + 1)))

                    self.moduleBasic[intConv * 2].bias.data.copy_(torch.utils.serialization.load_lua(
                        SpyNet_model_dir + '/modelL%d_%s-%d-bias.t7' % (intLevel + 1, arguments_strModel, intConv + 1)))
                # end

            # end

            def forward(self, tensorInput):
                return self.moduleBasic(tensorInput)
            # end

        # end

        class Backward(torch.nn.Module):
            def __init__(self, cuda_flag):
                super(Backward, self).__init__()
                self.cuda_flag = cuda_flag

            # end

            def forward(self, tensorInput, tensorFlow):
                # 如果还没定义tensorGrid or tensorGrid的某一维大小与tensorFlow的对应维度大小不一致 的话
                if hasattr(self, 'tensorGrid') == False or \
                        self.tensorGrid.size(0) != tensorFlow.size(0) or \
                        self.tensorGrid.size(2) != tensorFlow.size(2) or \
                        self.tensorGrid.size(3) != tensorFlow.size(3):
                    # initialize horizontal flow. 初始化水平flow网格
                    tensorHorizontal = torch.linspace(-1.0, 1.0, tensorFlow.size(3)).view(1, 1, 1,
                                                                                          tensorFlow.size(3)). \
                        expand(tensorFlow.size(0), -1, tensorFlow.size(2), -1)
                    # initialize vertical flow. 初始化垂直flow网格
                    tensorVertical = torch.linspace(-1.0, 1.0, tensorFlow.size(2)).view(1, 1,
                                                                                        tensorFlow.size(2), 1). \
                        expand(tensorFlow.size(0), -1, -1, tensorFlow.size(3))
                    # mix them into a original flow. 组合成初始flow网格
                    if self.cuda_flag:
                        self.tensorGrid = torch.cat([tensorHorizontal, tensorVertical], 1).cuda()
                    else:
                        self.tensorGrid = torch.cat([tensorHorizontal, tensorVertical], 1)
                # end

                tensorFlow = torch.cat([tensorFlow[:, 0:1, :, :] / ((tensorInput.size(3) - 1.0) / 2.0),
                                        tensorFlow[:, 1:2, :, :] / ((tensorInput.size(2) - 1.0) / 2.0)], 1)

                return torch.nn.functional.grid_sample(input=tensorInput,
                                                       grid=(self.tensorGrid + tensorFlow).permute(0, 2, 3, 1),
                                                       mode='bilinear', padding_mode='border')
            # end

        # end

        self.modulePreprocess = Preprocess()

        # initialize the weight of Gk in 6-layers pyramid. 初始化6层金字塔的Gk的权重
        self.moduleBasic = torch.nn.ModuleList([Basic(intLevel) for intLevel in range(4)])

        self.moduleBackward = Backward(cuda_flag=self.cuda_flag)

    # end

    def forward(self, tensorFirst, tensorSecond):
        tensorFlow = []
        tensorFirst = [self.modulePreprocess(tensorFirst)]  # apply rgb normalization
        tensorSecond = [self.modulePreprocess(tensorSecond)]  # apply rgb normalization

        for intLevel in range(3):
            # 最多下采样五次，意味着SpyNet最多6层(只要图片足够大，不小于32×~ or ~×32，不然下采样5次之后就变成一个像素点了)
            # downsample 5 times at most, meaning that SpyNet can be 6 layers at most.
            if tensorFirst[0].size(2) > 32 or tensorFirst[0].size(
                    3) > 32:  # if width and height are smaller than 32, then we won't apply downsampling on it.
                tensorFirst.insert(0, torch.nn.functional.avg_pool2d(input=tensorFirst[0], kernel_size=2,
                                                                     stride=2))  # d:average downsampling
                tensorSecond.insert(0, torch.nn.functional.avg_pool2d(input=tensorSecond[0], kernel_size=2,
                                                                      stride=2))  # d:average downsampling
            # end
        # end
        # 到这里tensorFirst/tensorSecond里就装着[d_5, d_4, d_3, d_2, d_1, 原图]了

        # initialize optical flow, all zero
        tensorFlow = tensorFirst[0].new_zeros(tensorFirst[0].size(0), 2,
                                              int(math.floor(tensorFirst[0].size(2) / 2.0)),
                                              int(math.floor(tensorFirst[0].size(3) / 2.0)))

        for intLevel in range(len(tensorFirst)):  # 循环金字塔level次
            # upsampling, enlarge 2 times, but I don't know why he multiplicative it with 2.0
            tensorUpsampled = torch.nn.functional.interpolate(input=tensorFlow, scale_factor=2, mode='bilinear',
                                                              align_corners=True) * 2.0

            # if the sizes of upsampling and downsampling are not the same, apply zero-padding.
            if tensorUpsampled.size(2) != tensorFirst[intLevel].size(2):
                tensorUpsampled = torch.nn.functional.pad(input=tensorUpsampled, pad=[0, 0, 0, 1],
                                                          mode='replicate')  # mode='replicate' 表示不改变原来的
            if tensorUpsampled.size(3) != tensorFirst[intLevel].size(3):
                tensorUpsampled = torch.nn.functional.pad(input=tensorUpsampled, pad=[0, 1, 0, 0],
                                                          mode='replicate')

            # input 输入网络：[first picture of corresponding level,
            # 			      the output of w with input second picture of corresponding level and upsampling flow,
            # 			      upsampling flow]
            # then we obtain the final flow. 最终再加起来得到intLevel的flow
            tensorFlow = self.moduleBasic[intLevel](torch.cat([tensorFirst[intLevel],
                                                               self.moduleBackward(tensorSecond[intLevel],
                                                                                   tensorUpsampled),
                                                               tensorUpsampled], 1)) + tensorUpsampled
        # end
        return tensorFlow


class STN4TOFlow(torch.nn.Module):
    def __init__(self):
        super(STN4TOFlow, self).__init__()

        # Spatial transformer localization-network
        self.localization4frame = nn.Sequential(
            nn.Conv2d(in_channels=3, out_channels=8, kernel_size=7),
            nn.MaxPool2d(2, stride=2),
            nn.ReLU(True),
            nn.Conv2d(in_channels=8, out_channels=10, kernel_size=5),
            nn.MaxPool2d(2, stride=2),
            nn.ReLU(True)
        )

        self.localization4flow = nn.Sequential(
            nn.Conv2d(in_channels=2, out_channels=8, kernel_size=7),
            nn.MaxPool2d(2, stride=2),
            nn.ReLU(True),
            nn.Conv2d(in_channels=8, out_channels=10, kernel_size=5),
            nn.MaxPool2d(2, stride=2),
            nn.ReLU(True)
        )

        self.localization = nn.Sequential(
            nn.Conv2d(in_channels=5, out_channels=8, kernel_size=7),
            nn.MaxPool2d(2, stride=2),
            nn.ReLU(True),
            nn.Conv2d(in_channels=8, out_channels=10, kernel_size=5),
            nn.MaxPool2d(2, stride=2),
            nn.ReLU(True)
        )

        # Regressor for the 3 * 2 affine matrix
        self.fc_loc = nn.Sequential(
            nn.Linear(in_features=10 * 60 * 108, out_features=32),
            nn.ReLU(True),
            nn.Linear(in_features=32, out_features=3 * 2)
        )

        # Initialize the weights/bias with identity transformation
        self.fc_loc[2].weight.data.fill_(0)
        self.fc_loc[2].bias.data = torch.FloatTensor([1, 0, 0, 0, 1, 0])

    # Spatial transformer network forward function
    def forward(self, frame, flow):
        # transform the input
        # frameflow = torch.cat((frame, flow), dim=1)
        # frameflow_loc = self.localization(frameflow)
        # frameflow_loc = frameflow_loc.view(-1, 10 * 60 * 108)
        # theta = self.fc_loc(frameflow_loc)
        # theta = theta.view(-1, 2, 3)
        #
        # frame_grid = torch.nn.functional.affine_grid(theta, frame.size())
        # frame = torch.nn.functional.grid_sample(frame, frame_grid)
        # flow_grid = torch.nn.functional.affine_grid(theta, flow.size())
        # flow = torch.nn.functional.grid_sample(flow, flow_grid)

        frame_loc = self.localization4frame(frame)
        frame_loc = frame_loc.view(-1, 10 * 60 * 108)
        # flow_loc = self.localization4flow(flow)
        # flow_loc = flow_loc.view(-1, 10 * 60 * 108)

        frame_theta = self.fc_loc(frame_loc)
        frame_theta = frame_theta.view(-1, 2, 3)
        # flow_theta = self.fc_loc(flow_loc)
        # flow_theta = flow_theta.view(-1, 2, 3)

        frame_grid = torch.nn.functional.affine_grid(frame_theta, frame.size())
        frame = torch.nn.functional.grid_sample(frame, frame_grid)
        # flow_grid = torch.nn.functional.affine_grid(flow_theta, flow.size())
        # flow = torch.nn.functional.grid_sample(flow, flow_grid)

        return frame, flow


class warp(torch.nn.Module):
    def __init__(self, h, w, cuda_flag):
        super(warp, self).__init__()
        self.height = h
        self.width = w
        if cuda_flag:
            self.addterm = self.init_addterm().cuda()
        else:
            self.addterm = self.init_addterm()

    def init_addterm(self):
        n = torch.FloatTensor(list(range(self.width)))
        horizontal_term = n.expand((1, 1, self.height, self.width))  # 第一个1是batch size
        n = torch.FloatTensor(list(range(self.height)))
        vertical_term = n.expand((1, 1, self.width, self.height)).permute(0, 1, 3, 2)
        addterm = torch.cat((horizontal_term, vertical_term), dim=1)
        return addterm

    def forward(self, frame, flow):
        """
        :param frame: frame.shape (batch_size=1, n_channels=3, width=256, height=448)
        :param flow: flow.shape (batch_size=1, n_channels=2, width=256, height=448)
        :return: reference_frame: predicted frame
        """
        # if self.addterm:
        if True:
            flow = flow + self.addterm
        else:
            self.addterm = self.init_addterm()
            flow = flow + self.addterm

        horizontal_flow = flow[0, 0, :, :].expand(1, 1, self.height, self.width)  # 第一个0是batch size
        vertical_flow = flow[0, 1, :, :].expand(1, 1, self.height, self.width)

        horizontal_flow = horizontal_flow * 2 / (self.width - 1) - 1
        vertical_flow = vertical_flow * 2 / (self.height - 1) - 1
        flow = torch.cat((horizontal_flow, vertical_flow), dim=1)
        flow = flow.permute(0, 2, 3, 1)
        reference_frame = torch.nn.functional.grid_sample(frame, flow)
        return reference_frame


class ResNet(torch.nn.Module):
    """
    Three-layers ResNet/ResBlock
    reference: https://blog.csdn.net/chenyuping333/article/details/82344334
    """

    def __init__(self):
        super(ResNet, self).__init__()
        self.conv1 = torch.nn.Conv2d(in_channels=3, out_channels=64, kernel_size=9, padding=8 // 2)
        self.conv2 = torch.nn.Conv2d(in_channels=64, out_channels=64, kernel_size=1)
        self.conv3 = torch.nn.Conv2d(in_channels=64, out_channels=3, kernel_size=1)

    def ResBlock(self, x):
        Fx = torch.nn.functional.relu(self.conv1(x))
        Fx = torch.nn.functional.relu(self.conv2(Fx))
        Fx = torch.nn.functional.relu(self.conv3(Fx))
        return Fx

    def forward(self, frames):
        aver = frames.mean(dim=1)
        result = self.ResBlock(aver)
        return result
        # return aver


class TOFlow(torch.nn.Module):
    def __init__(self, h, w, cuda_flag):
        super(TOFlow, self).__init__()
        self.height = h
        self.width = w
        self.cuda_flag = cuda_flag
        self.visualization = visualization()

        self.SpyNet = SpyNet(cuda_flag=self.cuda_flag)  # SpyNet层
        # for param in self.SpyNet.parameters():  # fix
        #     param.requires_grad = False

        self.STN4TOFlow = STN4TOFlow()

        self.warp = warp(self.height, self.width, cuda_flag=self.cuda_flag)

        self.ResNet = ResNet()

    # frameFirst, frameSecond should be TensorFloat
    def forward(self, frames, pltflag, epoch):
        """
        :param frames: [batch_size=1, img_num=7, n_channels=3, height=256, width=448]
        :return:
        """
        if self.cuda_flag:
            opticalflows = torch.empty(frames.size(0), frames.size(1), 2, frames.size(3), frames.size(4)).cuda()
            warpframes = torch.empty(frames.size(0), frames.size(1), 3, frames.size(3), frames.size(4)).cuda()
            stnframes = torch.empty(frames.size(0), frames.size(1), 3, frames.size(3), frames.size(4)).cuda()
            stnopticalflows = torch.empty(frames.size(0), frames.size(1), 2, frames.size(3), frames.size(4)).cuda()
        else:
            opticalflows = torch.empty(frames.size(0), frames.size(1), 2, frames.size(3), frames.size(4))
            warpframes = torch.empty(frames.size(0), frames.size(1), 3, frames.size(3), frames.size(4))
            stnframes = torch.empty(frames.size(0), frames.size(1), 3, frames.size(3), frames.size(4))
            stnopticalflows = torch.empty(frames.size(0), frames.size(1), 2, frames.size(3), frames.size(4))

        for i in [0, 1, 2, 4, 5, 6]:
            opticalflows[:, i, :, :, :] = self.SpyNet(frames[:, 3, :, :, :], frames[:, i, :, :, :])
        opticalflows[:, 3, :, :, :] = torch.zeros(frames.size(0), 2, frames.size(3), frames.size(4))
        # opticalflow: [batch_size=1, n_channels=2, h, w]
        # if pltflag:
        #     plt.imsave('./visualization/flow0%04d.png' % epoch,
        #                self.visualization.flow_to_image(opticalflows[0, 0, :, :, :].permute(1, 2, 0)))
        #     plt.imsave('./visualization/frame0%04d.png' % epoch,
        #                frames[0, 0, :, :, :].permute(1, 2, 0).cpu().detach().numpy())

        for i in range(7):
            stnframes[:, i, :, :, :], stnopticalflows[:, i, :, :, :] = \
                self.STN4TOFlow(frames[:, i, :, :, :], opticalflows[:, i, :, :, :])
            # stnframes[:, i, :, :, :], stnopticalflows[:, i, :, :, :] = \
            #     frames[:, i, :, :, :], opticalflows[:, i, :, :, :]
        # stnframes[:, 3, :, :, :] = frames[:, 3, :, :, :]
        # stnopticalflows[:, 3, :, :, :] = opticalflows[:, 3, :, :, :]
        # if pltflag:
        #     plt.imsave('./visualization/stnflow0%04d.png' % epoch,
        #                self.visualization.flow_to_image(stnopticalflows[0, 0, :, :, :].permute(1, 2, 0)))
        #     plt.imsave('./visualization/stnframe0%04d.png' % epoch,
        #                stnframes[0, 0, :, :, :].permute(1, 2, 0).cpu().detach().numpy())

        for i in [0, 1, 2, 4, 5, 6]:
            warpframes[:, i, :, :, :] = self.warp(stnframes[:, i, :, :, :], stnopticalflows[:, i, :, :, :])
        warpframes[:, 3, :, :, :] = stnframes[:, 3, :, :, :]
        # if pltflag:
        #     plt.imsave('./visualization/warpframe0%04d.png' % epoch,
        #                warpframes[0, 0, :, :, :].permute(1, 2, 0).cpu().detach().numpy())

        # warpframes: [batch_size=1, img_num=7, n_channels=3, height=256, width=448]

        Img = self.ResNet(warpframes)
        if pltflag:
            plt.imsave('./visualization/result%04d.png' % epoch,
                       Img[0, :, :, :].permute(1, 2, 0).cpu().detach().numpy())
        # Img = warpframes.mean(dim=1)
        # Img: [batch_size=1, n_channels=3, h, w]
        return Img
