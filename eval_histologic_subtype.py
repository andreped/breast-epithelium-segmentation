"""
Script for evaluating segmentations (invasive, benign, in situ) on case level and for each histologic subtype
and histologic grade
"""
import os
import numpy as np
import pandas as pd
import h5py
import tensorflow as tf
import multiprocessing as mp


def class_dice_(y_true, y_pred, class_val):
    output1 = y_pred[..., class_val]
    gt1 = y_true[..., class_val]

    intersection1 = tf.reduce_sum(output1 * gt1)
    union1 = tf.reduce_sum(output1 * output1) + tf.reduce_sum(
        gt1 * gt1)  # @TODO: why do we need output*output in reduce sum?
    if union1 == 0:
        dice = 1.  # used to be 0 before 28.05.23
        dice_u = True
    else:
        dice = (2. * intersection1) / union1
        dice_u = False

    return dice, dice_u


def eval_wrapper(some_inputs_):
    return eval_histological_subtype(*some_inputs_)


def eval_histological_subtype(path, model):
    import fast

    class PadderPO(fast.PythonProcessObject):
        def __init__(self, width=1024, height=1024):
            super().__init__()
            self.createInputPort(0)
            self.createOutputPort(0)

            self.height = height
            self.width = width

        def execute(self):
            # Get image and invert it with numpy
            image = self.getInputData()
            np_image = np.asarray(image)
            tmp = np.zeros((self.height, self.width, 3), dtype="uint8")
            shapes = np_image.shape
            tmp[:shapes[0], :shapes[1]] = np_image

            # Create new fast image and add as output
            new_output_image = fast.Image.createFromArray(tmp)
            new_output_image.setSpacing(image.getSpacing())
            self.addOutputData(0, new_output_image)

    with h5py.File(path, "r") as f:
        image = np.asarray(f["input"])
        gt = np.asarray(f["output"])

    image = image.astype("uint8")

    data_fast = fast.Image.createFromArray(image)
    generator = fast.PatchGenerator.create(2048, 2048, overlapPercent=0.3).connect(0, data_fast)
    padder = PadderPO.create(width=2048, height=2048).connect(generator)
    network = fast.NeuralNetwork.create(modelFilename=model, inferenceEngine="OpenVINO", scaleFactor=0.00392156862) \
        .connect(padder)
    converter = fast.TensorToSegmentation.create(threshold=0.5).connect(0, network, 5)
    resizer = fast.ImageResizer.create(width=2048, height=2048, useInterpolation=False, preserveAspectRatio=True) \
        .connect(converter)
    stitcher = fast.PatchStitcher.create().connect(resizer)

    for _ in fast.DataStream(stitcher):
        pass

    pred = stitcher.runAndGetOutputData()

    pred = np.asarray(pred)

    del data_fast, generator, padder, network, converter, resizer, stitcher

    gt_shape = gt.shape
    pred = pred[:gt_shape[0], :gt_shape[1]]
    gt = np.argmax(gt, axis=-1).astype("uint8")
    pred = pred[..., 0].astype("uint8")

    # one-hot gt and pred
    gt_back = (gt == 0).astype("float32")
    gt_inv = (gt == 1).astype("float32")
    gt_healthy = (gt == 2).astype("float32")
    gt_inSitu = (gt == 3).astype("float32")
    pred_back = (pred == 0).astype("float32")
    pred_inv = (pred == 1).astype("float32")
    pred_healthy = (pred == 2).astype("float32")
    pred_inSitu = (pred == 3).astype("float32")

    gt = np.stack(
        [gt_back, gt_inv,
         gt_healthy, gt_inSitu], axis=-1)
    pred = np.stack(
        [pred_back, pred_inv,
         pred_healthy, pred_inSitu], axis=-1)

    dice_scores = []
    class_names = ["invasive", "benign", "insitu"]
    for i, x in enumerate(class_names):
        c_dice, union_d = class_dice_(gt, pred, class_val=i + 1)
        dice_scores.append(c_dice)  # list of three dices, one for each class

    return np.asarray(dice_scores)


if __name__ == "__main__":
    os.environ["CUDA_VISIBLE_DEVICES"] = "2"

    d_set = "external"

    # stata path, file which includes info on what subtype each case is
    stata_path = '.../STATA.dta' # path to data file to use

    # dataset path
    dataset_path = '.../dataset/'  # path to dataset to use, TMA cores level 1
    df = pd.read_stata(stata_path, convert_categoricals=False)  # @TODO: this gives a warning, why?
    # type_vals = df["GRAD"].to_numpy().astype(int) # @TODO why does this not work

    # model path
    model_path = '.../model.onnx'  # path to model

    dice_types = [[[], [], []], [[], [], []], [[], [], []], [[], [], []], [[], [], []], [[], [], []], [[], [], []],
                  [[], [], []]]
    dice_grades = [[[], [], []], [[], [], []], [[], [], []]]

    for file in os.listdir(dataset_path):
        file_front = file.split(".")[0]
        splits = file_front.split("_")
        if d_set == "internal":
            cohort = splits[3]
            id_ = splits[4]
            case = splits[5]
        else:
            id_ = splits[4][1:]
            case = splits[7]

        # get matching case in stata file as TMA cylinder
        if d_set == "internal":
            filtered_data = df.loc[
                (df["Maren_P1"] == 1)
                & (df[str(cohort)] == 1)
                & (df["slide"] == int(id_))
                & (df["case"] == int(case))
                ]
        else:
            filtered_data = df.loc[
                (df["slide_" + str(id_)] == 1)
                & (df["case_" + str(id_)] == int(case))
                ]

        # needs to skip cylinders without information (excluded in STATA-file):
        if len(filtered_data) == 0:
            continue

        # get histological subtype and grade for case
        type_ = int(filtered_data["type_six"])  # 1, 2, 3, 4, 5, 8, still need eight in dice_types
        if 8 > type_ > 2:  # combine type 3, 4, 5 into type 8
            type_ = 8
        grade_ = int(filtered_data["GRAD"])

        # skip grade of types which are not 1-3 (grade) or 1, 2, 8 (type)
        if grade_ < 1 or grade_ > 3 or type_ < 1 or type_ > 8:
            continue

        inputs_ = [[dataset_path + file, model_path]]
        p = mp.Pool(1)
        output = p.map(eval_wrapper, inputs_)
        output = output[0]
        p.terminate()
        p.join()
        del p, inputs_

        class_names = ["invasive", "benign", "insitu"]
        for i, x in enumerate(class_names):
            dice_types[type_ - 1][i].append(output[i])
            dice_grades[grade_ - 1][i].append(output[i])

    print("len 1: ", len(dice_types[0][0]), len(dice_types[0][1]), len(dice_types[0][2]),
          " mu 1 inv: ", np.mean(dice_types[0][0]), " std 1 inv: ", np.std(dice_types[0][0], ddof=1),
          " mu 1 ben: ", np.mean(dice_types[0][1]), " std 1 ben: ", np.std(dice_types[0][1], ddof=1),
          " mu 1 ins: ", np.mean(dice_types[0][2]), " std 1 ins: ", np.std(dice_types[0][2], ddof=1))
    print("len 2: ", len(dice_types[1][0]), len(dice_types[1][1]), len(dice_types[1][2]),
          " mu 2 inv: ", np.mean(dice_types[1][0]), " std 2 inv: ", np.std(dice_types[1][0], ddof=1),
          " mu 2 ben: ", np.mean(dice_types[1][1]), " std 2 ben: ", np.std(dice_types[1][1], ddof=1),
          " mu 2 ins: ", np.mean(dice_types[1][2]), " std 2 ins: ", np.std(dice_types[1][2], ddof=1))
    print("len 3: ", len(dice_types[2][0]), len(dice_types[2][1]), len(dice_types[2][2]),
          " mu 3 inv: ", np.mean(dice_types[2][0]), " std 3 inv: ", np.std(dice_types[2][0], ddof=1),
          " mu 3 ben: ", np.mean(dice_types[2][1]), " std 3 ben: ", np.std(dice_types[2][1], ddof=1),
          " mu 3 ins: ", np.mean(dice_types[2][2]), " std 3 ins: ", np.std(dice_types[2][2], ddof=1))
    print("len 4: ", len(dice_types[3][0]), len(dice_types[3][1]), len(dice_types[3][2]),
          " mu 4 inv: ", np.mean(dice_types[3][0]), " std 4 inv: ", np.std(dice_types[3][0], ddof=1),
          " mu 4 ben: ", np.mean(dice_types[3][1]), " std 4 ben: ", np.std(dice_types[3][1], ddof=1),
          " mu 4 ins: ", np.mean(dice_types[3][2]), " std 4 ins: ", np.std(dice_types[3][2], ddof=1))
    print("len 5: ", len(dice_types[4][0]), len(dice_types[4][1]), len(dice_types[4][2]),
          " mu 5 inv: ", np.mean(dice_types[4][0]), " std 5 inv: ", np.std(dice_types[4][0], ddof=1),
          " mu 5 ben: ", np.mean(dice_types[4][1]), " std 5 ben: ", np.std(dice_types[4][1], ddof=1),
          " mu 5 ins: ", np.mean(dice_types[4][2]), " std 5 ins: ", np.std(dice_types[4][2], ddof=1))
    print("len 6: ", len(dice_types[5][0]), len(dice_types[5][1]), len(dice_types[5][2]),
          " mu 6 inv: ", np.mean(dice_types[5][0]), " std 6 inv: ", np.std(dice_types[5][0], ddof=1),
          " mu 6 ben: ", np.mean(dice_types[5][1]), " std 6 inv: ", np.std(dice_types[5][1], ddof=1),
          " mu 6 ins: ", np.mean(dice_types[5][2]), " std 6 ins: ", np.std(dice_types[5][2], ddof=1))
    print("len 7: ", len(dice_types[6][0]), len(dice_types[6][1]), len(dice_types[6][2]),
          " mu 7 inv: ", np.mean(dice_types[6][0]), " std 7 inv: ", np.std(dice_types[6][0], ddof=1),
          " mu 7 ben: ", np.mean(dice_types[6][1]), " std 7 ben: ", np.std(dice_types[6][1], ddof=1),
          " mu 7 ins: ", np.mean(dice_types[6][2]), " std 7 ins: ", np.std(dice_types[6][2], ddof=1))
    print("len 8: ", len(dice_types[7][0]), len(dice_types[7][1]), len(dice_types[7][2]),
          " mu 8 inv: ", np.mean(dice_types[7][0]), " std 8 inv: ", np.std(dice_types[7][0], ddof=1),
          " mu 8 ben: ", np.mean(dice_types[7][1]), " std 8 ben: ", np.std(dice_types[7][1], ddof=1),
          " mu 8 ins: ", np.mean(dice_types[7][2]), " std 8 ins: ", np.std(dice_types[7][2], ddof=1))
    print()
    print("len 1: ", len(dice_grades[0][0]), len(dice_grades[0][1]), len(dice_grades[0][2]),
          " mu 1 inv: ", np.mean(dice_grades[0][0]), " std 1 inv: ", np.std(dice_grades[0][0], ddof=1),
          " mu 1 ben: ", np.mean(dice_grades[0][1]), " std 1 ben: ", np.std(dice_grades[0][1], ddof=1),
          " mu 1 ins: ", np.mean(dice_grades[0][2]), " std 1 ins: ", np.std(dice_grades[0][2], ddof=1))
    print("len 2: ", len(dice_grades[1][0]),
          " mu 2 inv: ", np.mean(dice_grades[1][0]), " std 2 inv: ", np.std(dice_grades[1][0], ddof=1),
          " mu 2 ben: ", np.mean(dice_grades[1][1]), " std 2 ben: ", np.std(dice_grades[1][1], ddof=1),
          " mu 2 ins: ", np.mean(dice_grades[1][2]), " std 2 ins: ", np.std(dice_grades[1][2], ddof=1))
    print("len 3: ", len(dice_grades[2][0]),
          " mu 3 inv: ", np.mean(dice_grades[2][0]), " std 3 inv: ", np.std(dice_grades[2][0], ddof=1),
          " mu 3 ben: ", np.mean(dice_grades[2][1]), " std 3 ben: ", np.std(dice_grades[2][1], ddof=1),
          " mu 3 ins: ", np.mean(dice_grades[2][2]), " std 3 ins: ", np.std(dice_grades[2][2], ddof=1))



