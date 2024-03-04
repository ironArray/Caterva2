from collections.abc import Mapping

# Requirements
import blosc2
import h5py
import hdf5plugin  # enable Blosc2 support in HDF5


"""The registered identifier for Blosc2 in HDF5 filters."""
BLOSC2_HDF5_FID = 32026


# Warning: Keep the reference to the returned result.
# Losing the reference to the array may result in a segmentation fault.
def b2_from_h5chunk(h5_dset: h5py.Dataset,
                    chunk_index: int) -> (blosc2.NDArray | blosc2.SChunk):
    h5chunk_info = h5_dset.id.get_chunk_info(chunk_index)
    return blosc2.open(h5_dset.file.filename, mode='r',
                       offset=h5chunk_info.byte_offset)


def b2args_from_h5dset(h5_dset: h5py.Dataset) -> Mapping[str, object]:
    b2_args = dict(
        chunks=h5_dset.chunks,  # None is ok (let Blosc2 decide)
    )

    if h5_dset.chunks is None \
       or list(h5_dset._filters) != [f'{BLOSC2_HDF5_FID:#d}'] \
       or h5_dset.id.get_num_chunks() < 1:
        return b2_args

    # Blosc2 is the sole filter, direct chunk copy is possible.
    # Get Blosc2 arguments from the first schunk.
    # HDF5 filter parameters are less reliable than these.
    b2_array = b2_from_h5chunk(h5_dset, 0)
    b2_schunk = getattr(b2_array, 'schunk', b2_array)
    if hasattr(b2_array, 'blocks'):  # rely on cparams blocksize otherwise
        b2_args['blocks'] = b2_array.blocks
    b2_args['cparams'] = b2_schunk.cparams
    b2_args['dparams'] = b2_schunk.dparams

    return b2_args


def b2empty_from_h5dset(h5_dset: h5py.Dataset, b2_args={},
                        **kwds) -> blosc2.NDArray:
    b2_args = b2_args or b2args_from_h5dset(h5_dset)
    b2_array = blosc2.empty(shape=h5_dset.shape, dtype=h5_dset.dtype,
                            **(b2_args | kwds))
    return b2_array
