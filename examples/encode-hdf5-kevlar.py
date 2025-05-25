###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

# This script encodes the first nframes of the kevlar HDF5 dataset using Blosc2 with different codecs,
# including the GROK codec for JPEG2000 compression.  The output is saved to a new HDF5 file
# that can be opened with h5py and hdf5plugin; use b2h5py/PyTables to enable optimized slicing.
#
# For this to work, you will need to download the kevlar.h5 file from the kevlar repository:
# http://www.silx.org/pub/pyFAI/pyFAI_UM_2020/data_ID13/kevlar.h5
# More info on the kevlar dataset can be found here:
# https://hdf5plugin.readthedocs.io/en/stable/hdf5plugin_EuropeanHUG2022.html#A-look-at-performances-on-a-single-use-case

import os
import sys

import blosc2
import blosc2_grok
import h5py
import hdf5plugin
import numpy as np

clevel = 5  # compression level, e.g., 0-9, where 0 is no compression and 9 is maximum compression
cratio = 10  # compression ratio for JPEG2000 (grok)
nframes = 3  # number of frames to encode by default, e.g., 3, 10, 100, etc.
if len(sys.argv) > 1:
    try:
        nframes = int(sys.argv[1])
    except ValueError:
        print(f"Invalid number of frames: {sys.argv[1]}. Using default: {nframes} frames.")

fname_in = "kevlar.h5"  # input file with the kevlar dataset
fname_out = f"kevlar-blosc2-{nframes}frames.h5"

if not os.path.exists(fname_in):
    raise FileNotFoundError(
        f"Input file {fname_in} does not exist\n"
        "Please download it from the kevlar repository at:"
        " http://www.silx.org/pub/pyFAI/pyFAI_UM_2020/data_ID13/kevlar.h5"
    )


def encode_frames_grok(dset, fw):
    """Encode frames with blosc2-grok and save to HDF5 file."""
    # Define the compression and decompression parameters for Blosc2.
    # Disable the filters and the splitmode, because these don't work with grok.
    cparams = {
        "codec": blosc2.Codec.GROK,
        "filters": [],
        "splitmode": blosc2.SplitMode.NEVER_SPLIT,
    }
    # Set the parameters that will be used by grok
    kwargs = {
        "cod_format": blosc2_grok.GrkFileFmt.GRK_FMT_JP2,
        "num_threads": 1,  # this does not have any effect (grok should work in multithreading mode)
        "quality_mode": "rates",
        "quality_layers": np.array([cratio], dtype=np.float64),
    }
    blosc2_grok.set_params_defaults(**kwargs)

    b2comp = hdf5plugin.Blosc2()  # just for identification, no compression algorithm specified
    dset_out = g.create_dataset(
        "cname-grok",
        (nframes,) + dset.shape[1:],
        dset.dtype,
        chunks=(1,) + dset.shape[1:],  # chunk size of 1 frame
        **b2comp,
    )
    for i in range(nframes):
        im = dset[i : i + 1]
        # Transform the numpy array to a blosc2 array. This is where compression happens.
        b2im = blosc2.asarray(im, chunks=im.shape, blocks=im.shape, cparams=cparams)
        # Write to disk
        dset_out.id.write_direct_chunk((i, 0, 0), b2im.schunk.to_cframe())

    return dset_out


with h5py.File(fname_in, "r") as fr:
    dset = fr["/entry/data/data"]
    with h5py.File(fname_out, "w") as fw:
        g = fw.create_group("/data")
        for cname in ("blosclz", "lz4", "zstd", "grok"):
            if cname == "grok":
                # For grok, we need to encode the frames with the grok codec.
                # The grok codec is not available in the hdf5plugin.Blosc2 class,
                # so we use blosc2 directly.
                dset_out = encode_frames_grok(dset, fw)
            else:
                # For other codecs, we can use the hdf5plugin.Blosc2 class.
                b2comp = hdf5plugin.Blosc2(cname=cname, clevel=clevel, filters=hdf5plugin.Blosc2.BITSHUFFLE)
                dset_out = g.create_dataset(
                    f"cname-{cname}",
                    data=dset[:nframes],
                    dtype=dset.dtype,
                    chunks=(1,) + dset.shape[1:],  # chunk size of 1 frame
                    **b2comp,
                )
            print("dset ready:", fw.filename, dset_out)
