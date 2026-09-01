import blosc2
import numpy as np

data = np.arange(1_000_000).reshape(10, 100_000)
blosc2.asarray(data, chunks=(1, 100_000), urlpath="/data/public/mc.b2nd", mode="w")
print("seeded ok")
