import time
from typing import Union, Optional

import numpy as np
import os
from sklearn import metrics
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.tree import DecisionTreeClassifier

from decision_trees.utils.constants import ClassifierType
from decision_trees.vhdl_generators.tree import Tree
from decision_trees.vhdl_generators.random_forest import RandomForest
from decision_trees.utils.convert_to_fixed_point import quantize_data
from decision_trees.utils.constants import get_classifier


def test_dataset(
        number_of_bits_per_feature: int,
        train_data: np.ndarray, train_target: np.ndarray,
        test_data: np.ndarray, test_target: np.ndarray,
        clf_type: ClassifierType,
        max_depth: Optional[int], number_of_classifiers: Optional[int],
        path: str, name: str
):
    path = os.path.join(
        path,
        name + '_' + str(number_of_bits_per_feature) + '_' + clf_type.name + '_' + str(max_depth) + '_' + str(number_of_classifiers)
    )
    result_file = os.path.join(path, 'score.txt')

    if not os.path.exists(path):
        os.makedirs(path)

    # first create classifier from scikit
    clf = get_classifier(clf_type, max_depth, number_of_classifiers)

    # first - train the classifiers on non-quantized data
    clf.fit(train_data, train_target)
    test_predicted = clf.predict(test_data)
    print("scikit clf with test data:")
    report_performance(clf, clf_type, test_target, test_predicted, result_file)

    # perform quantization of train and test data
    # while at some point I was considering not quantizing the test data,
    # I came to a conclusion that it is not the way it will be performed in hardware
    train_data_quantized, test_data_quantized = quantize_data(
        train_data, test_data, number_of_bits_per_feature,
        flag_save_details_to_file=True, path=path
    )

    clf.fit(train_data_quantized, train_target)
    test_predicted_quantized = clf.predict(test_data_quantized)
    print("scikit clf with train and test data quantized:")
    report_performance(clf, clf_type, test_target, test_predicted_quantized, result_file)

    # generate own classifier based on the one from scikit
    number_of_features = len(train_data[0])
    # TODO(MF): this +1 is very important because the comparison values are not quantized
    my_clf = generate_my_classifier(clf, number_of_features, number_of_bits_per_feature+1, path)
    my_clf_test_predicted_quantized = my_clf.predict(test_data_quantized)
    print("own clf with train and test data quantized:")
    report_performance(my_clf, clf_type, test_target, my_clf_test_predicted_quantized)

    differences_scikit_my = np.sum(test_predicted_quantized != my_clf_test_predicted_quantized)
    print(f"Number of differences between scikit_qunatized and my_quantized: {differences_scikit_my}")

    # check if own classifier works the same as scikit one
    _compare_with_own_classifier(
        [test_predicted, test_predicted_quantized, my_clf_test_predicted_quantized],
        ["scikit", "scikit_quantized", "own_clf_quantized"],
        test_target,
        flag_save_details_to_file=True, path=path
    )

    # optionally check the performance of the scikit classifier for reference (does not work for own classifier)
    # _test_classification_performance(clf, test_data, 10, 10)


def report_performance(
        clf, clf_type: ClassifierType,
        expected: np.ndarray, predicted: np.ndarray,
        result_file: Optional[str]=None
):
    if clf_type == ClassifierType.RANDOM_FOREST_REGRESSOR:
        _report_regressor(expected, predicted, result_file)
    else:
        _report_classifier(clf, expected, predicted, result_file)


def _report_classifier(
        clf,
        expected: np.ndarray, predicted: np.ndarray,
        result_file: Optional[str]=None
):
    t = ''
    t += 'Detailed classification report:\n'

    t += 'Classification report for classifier ' + str(clf) + '\n'
    t += str(metrics.classification_report(expected, predicted)) + '\n'
    cm = metrics.confusion_matrix(expected, predicted)
    cm = cm / cm.sum(axis=1)[:, None] * 100

    # np.set_printoptions(formatter={'float': '{: 2.2f}'.format})
    t += f'Confusion matrix:\n {cm}\n'

    f1_score = metrics.f1_score(expected, predicted, average='weighted')
    precision = metrics.precision_score(expected, predicted, average='weighted')
    recall = metrics.recall_score(expected, predicted, average='weighted')
    accuracy = metrics.accuracy_score(expected, predicted)
    t += f'f1_score: {f1_score:{2}.{4}}\n'
    t += f'precision: {precision:{2}.{4}}\n'
    t += f'recall: {recall:{2}.{4}}\n'
    t += f'accuracy: {accuracy:{2}.{4}}\n'

    if result_file is not None:
        with open(result_file, 'a+') as f:
            f.write(t)
    else:
        print(t)


def _report_regressor(
        expected: np.ndarray,
        predicted: np.ndarray,
        result_file: Optional[str]=None
):
    t = ''
    t += 'Detailed regression report:\n'

    mae = metrics.mean_absolute_error(expected, predicted)
    mse = metrics.mean_squared_error(expected, predicted)
    r2s = metrics.r2_score(expected, predicted)
    evs = metrics.explained_variance_score(expected, predicted)
    t += f'mean_absolute_error: {mae:{2}.{4}}\n'
    t += f'mean_squared_error: {mse:{2}.{4}}\n'
    t += f'coefficient_of_determination: {r2s:{2}.{4}}\n'
    t += f'explained_variance_score: {evs:{2}.{4}}\n'

    if result_file is not None:
        with open(result_file, 'a+') as f:
            f.write(t)
    else:
        print(t)


def generate_my_classifier(
        clf,
        number_of_features: int,
        number_of_bits_per_feature: int,
        path: str,
        result_file: Union[str, None]=None
):
    if isinstance(clf, DecisionTreeClassifier):
        print("Creating decision tree classifier!")
        my_clf = Tree("DecisionTreeClassifier", number_of_features, number_of_bits_per_feature)
    elif isinstance(clf, RandomForestClassifier):
        print("Creating random forest classifier!")
        my_clf = RandomForest("RandomForestClassifier", number_of_features, number_of_bits_per_feature)
    else:
        print("Unknown type of classifier!")
        raise ValueError("Unknown type of classifier!")

    my_clf.build(clf)
    my_clf.create_vhdl_file(path)
    my_clf.print_parameters(result_file)

    return my_clf


def _compare_with_own_classifier(results: [], results_names: [str],
                                 test_target,
                                 flag_save_details_to_file: bool = True,
                                 path: str = "./"
                                 ):
    flag_no_errors = True
    number_of_errors = np.zeros(len(results))

    comparision_file = None
    if flag_save_details_to_file:
        comparision_file = open(path + "/comparision_details.txt", "w")

    for j in range(0, len(test_target)):
        flag_iteration_error = False

        for i in range(0, len(results)):
            if results[i][j] != test_target[j]:
                number_of_errors[i] += 1
                flag_no_errors = False
                flag_iteration_error = True

        if flag_iteration_error and flag_save_details_to_file:
            print("Difference between versions!", file=comparision_file)
            print("Ground true: " + str(test_target[j]), file=comparision_file)
            for i in range(0, len(results)):
                print(f"{results_names[i]}: {results[i][j]}", file=comparision_file)

    if flag_no_errors:
        print("All results were the same")
    else:
        for i in range(0, len(results)):
            print(f"Number of {results_names[i]} errors: {number_of_errors[i]}")


def _test_classification_performance(clf, test_data, number_of_data_to_test=1000, number_of_iterations=1000):
    if number_of_data_to_test <= len(test_data):
        start = time.clock()

        for i in range(0, number_of_iterations):
            for data in test_data[:number_of_data_to_test]:
                clf.predict([data])

        end = time.clock()
        elapsed_time = (end - start)

        print("It takes " +
              '% 2.4f' % (elapsed_time / number_of_iterations) +
              "us to classify " +
              str(number_of_data_to_test) + " data.")
    else:
        print("There is not enough data provided to evaluate the performance. It is required to provide at least " +
              str(number_of_data_to_test) + " values.")


# there is no general method for normalisation, so it was moved to be a part of each dataset
def normalise_data(train_data: np.ndarray, test_data: np.ndarray):
    from sklearn import preprocessing

    print("np.max(train_data): " + str(np.max(train_data)))
    print("np.ptp(train_data): " + str(np.ptp(train_data)))

    normalised_1 = 1 - (train_data - np.max(train_data)) / -np.ptp(train_data)
    normalised_2 = preprocessing.minmax_scale(train_data, axis=1)

    print(train_data[0])

    train_data /= 16
    test_data /= 16

    print("Are arrays equal: " + str(np.array_equal(normalised_2, train_data)))
    print("Are arrays equal: " + str(np.array_equal(normalised_1, train_data)))

    for i in range(0, 1):
        print(train_data[i])
        print(normalised_1)
        print(normalised_2)
