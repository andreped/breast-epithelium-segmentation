import fast
import numpy as np
from tqdm import tqdm
import matplotlib.pyplot as plt


# --- HYPER PARAMS
plot_flag = True
level = 2



#Import CK and annotated (in qupath) image:
importerCK = fast.WholeSlideImageImporter.create(
    '')  # path to CK image
importerMask = fast.TIFFImagePyramidImporter.create(
    '')  # path to annotated image

# access annotated mask (generated from qupath, blue channel)
image_CK = importerMask.runAndGetOutputData()
access = image_CK.getAccess(fast.ACCESS_READ)

# Get CK TMA cores
extractor = fast.TissueMicroArrayExtractor.create(level=level).connect(importerCK)
CK_TMAs = []
for j, TMA in tqdm(enumerate(fast.DataStream(extractor)), "CK TMA:"):
    CK_TMAs.append(TMA)
    if j == 20:
        break

CK_counter = 0
for element in CK_TMAs:

    CK_TMA = CK_TMAs[CK_counter]
    position_CK = CK_TMA.getTransform().getTranslation()  # position of IHC TMA at position IHC_counter. just zero, why?

    position_CK_x = position_CK[0]
    position_CK_y = position_CK[1]
    position_CK_z = position_CK[2]

    height, width, _ = CK_TMA.shape

    mask = access.getPatchAsImage(level, position_CK_x, position_CK_y, width, height, False)[..., :3]
    mask = np.asarray(mask)

    # plot CK tma core and mask:
    if plot_flag:
        plt.rcParams.update({'font.size': 28})

        f, axes = plt.subplots(1, 2, figsize=(30, 30))  # Figure of patches

        titles = ["CK TMA core", "mask from QuPath, blue channel"]
        axes[0, 0].imshow(CK_TMA, interpolation='none')
        axes[0, 1].imshow(mask, cmap="gray", interpolation='none')

        cnts = 0
        for i in range(1):
            for j in range(2):
                axes[i, j].set_title(titles[cnts])
                cnts += 1

        plt.tight_layout()
        plt.show()

    CK_counter += 1