###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""
Script for exporting a series of images into a Blosc2 HDF5 file using the Grok codec.
"""

import blosc2_grok
import h5py
import hdf5plugin
import numpy as np
from PIL import Image
import os

import blosc2

if __name__ == '__main__':
    cratio = 10
    print(f"Compressing with cratio={cratio}x ...")

    # Define the compression and decompression parameters. Disable the filters and the
    # splitmode, because these don't work with the codec.
    cparams = {
        'codec': blosc2.Codec.GROK,
        'nthreads': 1,
        'filters': [],
        'splitmode': blosc2.SplitMode.NEVER_SPLIT,
    }
    # Set the parameters that will be used by grok
    kwargs = {
        'cod_format': blosc2_grok.GrkFileFmt.GRK_FMT_JP2,
        'num_threads': 1,    # this does not have any effect (grok should work in multithreading mode)
        'quality_mode': "rates",
        'quality_layers': np.array([cratio], dtype=np.float64)
    }
    blosc2_grok.set_params_defaults(**kwargs)

    # Compression params for identifying the codec. In the future, one should be able to
    # specify the grok plugin (and its parameters) here.
    b2params = hdf5plugin.Blosc2()

    # Open the PNG datasets
    dir_path = 'numbers'
    # List of PNG files (in order)
    png_files = [
        "zero.png",
        "one.png",
        "two.png",
        "three.png",
        "four.png",
        "five.png",
        "six.png",
        "seven.png",
        "eight.png",
        "nine.png",
    ]
    images_color = []
    images_gray = []
    for png_file in png_files:
        # Open the image file
        img_color = Image.open(os.path.join(dir_path, png_file))
        images_color.append(np.array(img_color))
        # Convert the image to grayscale
        img_gray = img_color.convert('L')
        images_gray.append(np.array(img_gray))
    dset_color = np.array(images_color)
    dset_gray = np.array(images_gray)

    # Open the output file for color and gray images
    fname = f'numbers-{cratio}x.h5'
    print(f"output file: {fname}")
    fout = h5py.File(fname, 'w')

    # Store the color images
    chunks = (1,) + dset_color.shape[1:]
    disk_color = fout.create_dataset('/numbers_color', shape=dset_color.shape, dtype=dset_color.dtype,
                                     chunks=chunks, **b2params)
    disk_color.attrs['contenttype'] = 'tomography'
    for i in range(dset_color.shape[0]):
        im = dset_color[i:i+1]
        b2im = blosc2.asarray(im, chunks=im.shape, blocks=im.shape, cparams=cparams)
        # Write to disk
        disk_color.id.write_direct_chunk((i, 0, 0, 0), b2im.schunk.to_cframe())

    # The gray images
    chunks = (1,) + dset_gray.shape[1:]
    disk_gray = fout.create_dataset('/numbers_gray', shape=dset_gray.shape, dtype=dset_gray.dtype,
                                    chunks=chunks, **b2params)
    disk_gray.attrs['contenttype'] = 'tomography'
    for i in range(dset_gray.shape[0]):
        im = dset_gray[i:i+1]
        b2im = blosc2.asarray(im, chunks=im.shape, blocks=im.shape, cparams=cparams)
        # Write to disk
        disk_gray.id.write_direct_chunk((i, 0, 0), b2im.schunk.to_cframe())
    fout.close()
