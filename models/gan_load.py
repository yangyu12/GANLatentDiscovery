import json
import numpy as np
import torch
from torch import nn
from models.BigGAN import BigGAN, utils
from models.ProgGAN.model import Generator as ProgGenerator
from models.SNGAN.load import load_model_from_state_dict
from models.gan_with_shift import gan_with_shift

try:
    from models.StyleGAN2.model import Generator as StyleGAN2Generator
except Exception as e:
    print('StyleGAN2 load fail: {}'.format(e))


class ConditionedBigGAN(nn.Module):
    def __init__(self, big_gan, target_classes=(239)):
        super(ConditionedBigGAN, self).__init__()
        self.big_gan = big_gan

        self.target_classes = nn.Parameter(torch.tensor(target_classes, dtype=torch.int64),
            requires_grad=False)

        self.dim_z = self.big_gan.dim_z

    def set_classes(self, cl):
        try:
            cl[0]
        except Exception:
            cl = [cl]
        self.target_classes.data = torch.tensor(cl, dtype=torch.int64)

    def mixed_classes(self, batch_size):
        if len(self.target_classes.data.shape) == 0:
            return self.target_classes.repeat(batch_size).cuda()
        else:
            return torch.from_numpy(
                np.random.choice(self.target_classes.cpu(), [batch_size])).cuda()

    def forward(self, z, classes=None):
        if classes is None:
            classes = self.mixed_classes(z.shape[0]).to(z.device)
        return self.big_gan(z, self.big_gan.shared(classes))


class UnconditionalBigGAN(nn.Module):
    def __init__(self, big_gan):
        super(UnconditionalBigGAN, self).__init__()
        self.big_gan = big_gan
        self.dim_z = self.big_gan.dim_z

    def forward(self, z):
        classes = torch.zeros(z.shape[0], dtype=torch.int64, device=z.device)
        return self.big_gan(z, self.big_gan.shared(classes))


class StyleGAN2Wrapper(nn.Module):
    def __init__(self, g, shift_in_w):
        super(StyleGAN2Wrapper, self).__init__()
        self.style_gan2 = g
        self.shift_in_w = shift_in_w
        self.dim_z = 512
        self.dim_shift = self.style_gan2.style_dim if shift_in_w else self.dim_z

    def forward(self, input, input_is_latent=False):
        return self.style_gan2([input], input_is_latent=input_is_latent)[0]

    def gen_shifted(self, z, shift):
        if self.shift_in_w:
            w = self.style_gan2.get_latent(z)
            return self.forward(w + shift, input_is_latent=True)
        else:
            return self.forward(z + shift, input_is_latent=False)


def make_biggan_config(weights_root):
    with open('models/BigGAN/generator_config.json') as f:
        config = json.load(f)
    config['weights_root'] = weights_root
    return config


@gan_with_shift
def make_big_gan(weights_root, target_class):
    config = make_biggan_config(weights_root)

    config['resolution'] = utils.imsize_dict[config['dataset']]
    config['n_classes'] = utils.nclass_dict[config['dataset']]
    config['G_activation'] = utils.activation_dict[config['G_nl']]
    config['D_activation'] = utils.activation_dict[config['D_nl']]
    config['skip_init'] = True
    config['no_optim'] = True

    G = BigGAN.Generator(**config)
    G.load_state_dict(torch.load(config['weights_root'], map_location=torch.device('cpu')),
                      strict=True)

    return ConditionedBigGAN(G, target_class).cuda()


@gan_with_shift
def make_big_bi_gan_as_big_gan(weights_root, resolution=128):
    attn_dict = {128: '64', 256: '128', 512: '64'}
    dim_z_dict = {128: 120, 256: 140, 512: 128}
    config = {
        'G_param': 'SN', 'D_param': 'SN',
        'G_ch': 96, 'D_ch': 96,
        'D_wide': True, 'G_shared': True,
        'shared_dim': 128, 'dim_z': dim_z_dict[resolution],
        'hier': True, 'cross_replica': False,
        'mybn': False, 'G_activation': nn.ReLU(inplace=True),
        'G_attn': attn_dict[resolution],
        'norm_style': 'bn',
        'G_init': 'ortho', 'skip_init': True, 'no_optim': True,
        'G_fp16': False, 'G_mixed_precision': False,
        'accumulate_stats': False, 'num_standing_accumulations': 16,
        'G_eval_mode': True,
        'BN_eps': 1e-04, 'SN_eps': 1e-04,
        'num_G_SVs': 1, 'num_G_SV_itrs': 1, 'resolution': resolution,
        'n_classes': 1000}

    G = BigGAN.Generator(**config)
    G.load_state_dict(torch.load(weights_root, map_location=torch.device('cpu')), strict=False)

    return UnconditionalBigGAN(G).cuda()


@gan_with_shift
def make_proggan(weights_root):
    model = ProgGenerator()
    model.load_state_dict(torch.load(weights_root))
    model.cuda()

    setattr(model, 'dim_z', [512, 1, 1])
    return model


@gan_with_shift
def make_sngan(gan_dir):
    gan = load_model_from_state_dict(gan_dir)
    G = gan.model.eval()
    setattr(G, 'dim_z', gan.distribution.dim)

    return G


def make_style_gan2(size, weights, shift_in_w=True):
    G = StyleGAN2Generator(size, 512, 8)
    G.load_state_dict(torch.load(weights)['g_ema'])
    G.cuda().eval()

    return StyleGAN2Wrapper(G, shift_in_w=shift_in_w)
