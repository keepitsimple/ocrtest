import csv
import os
import random
import logging

from sklearn.externals.joblib import delayed, Parallel

from process_image import Image, process_single_image


def process_n_cut_single_image(filename, tagPosition, negativeMultiplicator=3, positiveImageTemplate=None):
    positive, negative = process_single_image(filename, tagPosition, positiveImageTemplate)

    # todo: fix multi-threading bug with using same global random object
    random.shuffle(negative)
    negativeMAmount = int(len(positive) * negativeMultiplicator)
    if negativeMAmount < len(negative):
        negative = negative[:negativeMAmount]

    return filename, positive, negative


class DatasetCreator(object):
    def __init__(self):
        self.logger = logging.getLogger("RawFeatureExtractor")
        self.trainDataset = []
        self.testDataset = []
        self.trainFilenames = []
        self.testFilenames = []
        self.trainParallelTasks = []
        self.testParallelTasks = []

    def _prepareParallelTasks(self, files, tagPosition, negativeMultiplicator, interestingWindowsFolder=None):
        taskQueue = []
        for filename in files:
            if interestingWindowsFolder:
                path, rfilename = os.path.split(filename)
                name, extension = os.path.splitext(rfilename)
                positiveImageTemplate = os.path.join(interestingWindowsFolder, name + '_%d' + extension)
            else:
                positiveImageTemplate = None

            taskQueue.append(delayed(process_n_cut_single_image)(filename, tagPosition, negativeMultiplicator, positiveImageTemplate))

        return taskQueue

    def prepareImageProcessing(self, trainFiles, testFiles, tagPosition, negativeMultiplicator=3, interestingWindowsFolder=None):
        if interestingWindowsFolder and not os.path.exists(interestingWindowsFolder):
            os.makedirs(interestingWindowsFolder)

        self.trainFilenames.extend(trainFiles)
        self.testFilenames.extend(testFiles)

        self.trainParallelTasks.extend(self._prepareParallelTasks(trainFiles, tagPosition, negativeMultiplicator, interestingWindowsFolder))
        self.testParallelTasks.extend(self._prepareParallelTasks(testFiles, tagPosition, negativeMultiplicator, interestingWindowsFolder))

    def _processResult(self, features, dataset):
        for filename, positive, negative in features:
            if len(positive) == 0:
                self.logger.warning('No positive windows were created in image: %s' % (filename,))

            for e in positive:
                # dataset.append(np.concatenate([e, np.array([1])]))
                dataset.append(list(e) + [1])
            for e in negative:
                # dataset.append(np.concatenate([e, np.array([0])]))
                dataset.append(list(e) + [0])

    def processPrepared(self, jobs=-1):
        p = Parallel(n_jobs=jobs, verbose=100, pre_dispatch='3*n_jobs')
        trainFeatures = p(self.trainParallelTasks)
        testFeatures = p(self.testParallelTasks)
        self._processResult(trainFeatures, self.trainDataset)
        self._processResult(testFeatures, self.testDataset)

    def saveCSV(self, trainFilename, testFilename):
        random.shuffle(self.trainDataset)
        random.shuffle(self.testDataset)

        with open(trainFilename, 'wb') as f:
            writer = csv.writer(f)
            writer.writerows(self.trainDataset)

        with open(testFilename, 'wb') as f:
            writer = csv.writer(f)
            writer.writerows(self.testDataset)

    def saveTrainTestImageFilenames(self, trainImagesFilenames, testImagesFilenames):
        with open(trainImagesFilenames, 'wb') as f:
            writer = csv.writer(f)
            writer.writerows([(i,) for i in self.trainFilenames])

        with open(testImagesFilenames, 'wb') as f:
            writer = csv.writer(f)
            writer.writerows([(i,) for i in self.testFilenames])


if __name__ == '__main__':
    import ocr_utils
    ocr_utils.init_console_logging()

    import sys
    if len(sys.argv) < 4:
        print 'USAGE:\n\t' + sys.argv[0] + ' folderWithImages train.csv test.csv [trainFiles.csv testFiles.csv]'
        print '\nfolderWithImages name format: folderName_X1xY1xX2xY2, where X1xY1xX2xY2 coordinates of rectangle with hashtag'
        sys.exit(1)

    d = DatasetCreator()
    d.directoryProcess(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[1]) + '_interesting')
    d.saveCSV(sys.argv[2], sys.argv[3])

    if len(sys.argv) >= 6:
        d.saveTrainTestImageFilenames(sys.argv[4], sys.argv[5])
