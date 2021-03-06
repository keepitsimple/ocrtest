import argparse
import csv
import logging

import numpy as np
from sklearn import metrics, cross_validation
from sklearn.externals import joblib
from sklearn.grid_search import GridSearchCV, RandomizedSearchCV, _CVScoreTuple

try:
    from pybrain import optimization
    from pybrain.optimization.populationbased.pso import Particle
except ImportError:
    optimization = None
    Particle = None

from ocr_utils import loadDataset, saveClassifiersEvaluations
from parameters_optimization.classifier_evaluation import ClassifierEvaluation
from parameters_optimization.parallel_pso import ParallelParticleSwarmOptimizer


class Evaluator(object):
    def __init__(self, parameterNames, defaultParameters, parameters_restrictions, classifierFactory, trainData, trainLabel, cv=2):
        self.parameterNames = parameterNames
        self.defaultParameters = defaultParameters
        self.parameters_restrictions = parameters_restrictions
        self.classifierFactory = classifierFactory
        self.trainData = trainData
        self.trainLabel = trainLabel
        self.cv = cv

        self.best_score_ = None
        self.best_params_ = None
        self.best_classifier = None

        self.grid_scores_ = []

    def __call__(self, params):
        p = dict()
        invalid_parameters = False
        failed_power = 3.0
        for i, name in enumerate(self.parameterNames):
            p[name] = params[i]
            if self.parameters_restrictions and name in self.parameters_restrictions:
                if not self.parameters_restrictions[name](params[i]):
                    invalid_parameters = True
                    failed_power *= params[i]
        p.update(self.defaultParameters)
        print p
        if invalid_parameters:
            print 'invalid: ', -abs(failed_power)
            return -abs(failed_power)
        clf = self.classifierFactory(**p)
        scores = cross_validation.cross_val_score(clf, self.trainData, self.trainLabel, cv=self.cv, scoring='f1')
        print scores.mean()
        #todo: think about putting all scores to array/list
        self.grid_scores_.append(_CVScoreTuple(p, scores.mean(), scores))
        if self.best_score_ is None or self.best_score_ < scores.mean():
            self.best_score_ = scores.mean()
            self.best_params_ = p
        return scores.mean()

    def getBestClassifier(self):
        if self.best_classifier is not None:
            return self.best_classifier
        if self.best_params_ is None:
            return None
        classifier = self.classifierFactory(**self.best_params_)
        classifier.fit(self.trainData, self.trainLabel)

        self.best_classifier = classifier
        return classifier

    best_estimator_ = property(getBestClassifier)


class MetaOptimizer(object):
    def __init__(self, process_args=True):
        self.logger = logging.getLogger("MetaOptimizer")
        if process_args:
            self.process_arguments()
        self.classifier = self.classifierFactory()

    def process_arguments(self):
        parser = argparse.ArgumentParser(description='Classifier meta-parameter optimization')
        parser.add_argument('train', help='Train dataset')
        parser.add_argument('test', help='Test dataset')
        parser.add_argument('model', help='File to save best model')
        parser.add_argument('scores', default=None, help='File to save scores of tested models')
        parser.add_argument('-t', '--type', default='grid', choices=['grid', 'random', 'pso'], help='Search type')
        parser.add_argument('-i', '--iterations', default=self.iterations, type=int, help='Iterations amount for pso and random search')
        parser.add_argument('-j', '--jobs', default=-1, type=int, help='Processes amount for learning')

        args = parser.parse_args()

        trainData, trainLabel = loadDataset(args.train)
        testData, testLabel = loadDataset(args.test)

        self.initialize_optimizer(args.type, args.model, trainData, trainLabel, testData, testLabel, args.jobs, args.iterations, args.scores)

    def initialize_optimizer(self, optimizationMethod, modelFilename, trainData, trainLabel, testData, testLabel, jobs, iterations=None, scoresCsvFilename=None):
        self.optimizationMethod = optimizationMethod
        self.modelFilename = modelFilename
        self.trainData, self.trainLabel = trainData, trainLabel
        self.testData, self.testLabel = testData, testLabel
        self.jobs = jobs
        if iterations is not None:
            self.iterations = iterations
        self.scoresCsvFilename = scoresCsvFilename

        optimizationAlgorithms = {
            'grid': self.grid_search,
            'random': self.randomized_search,
            'pso': self.pso_search,
        }
        self.algorithm = optimizationAlgorithms[self.optimizationMethod]

    def grid_search(self):
        search = GridSearchCV(self.classifier, self.grid_parameters, cv=2,  scoring='f1', n_jobs=self.jobs)
        search.fit(self.trainData, self.trainLabel)

        return search

    def randomized_search(self):
        rnd_search = RandomizedSearchCV(
            self.classifier,
            self.randomized_parameters,
            n_iter=self.iterations,
            cv=2,
            scoring='f1',
            n_jobs=self.jobs
        )
        rnd_search.fit(self.trainData, self.trainLabel)

        return rnd_search

    def pso_search(self):
        if optimization is None:
            raise Exception("PyBrain is not installed")

        mutable_parameters = []
        immutable_parameters = {}
        for name, variants in self.grid_parameters.items():
            if len(variants) > 1:
                mutable_parameters.append(name)
            elif len(variants) == 1:
                immutable_parameters[name] = variants[0]

        co = Evaluator(mutable_parameters, immutable_parameters, self.pso_parameters_restrictions, self.classifierFactory, self.trainData, self.trainLabel)
        x0 = np.array([0] * len(mutable_parameters))
        psoo = ParallelParticleSwarmOptimizer(self.jobs, co, x0, boundaries=[(0, 1)] * len(mutable_parameters), size=5)
        psoo.maxEvaluations = self.iterations
        psoo.particles = []
        for _ in xrange(psoo.size):
            startingPosition = []
            for name in mutable_parameters:
                startingPosition.append(self.randomized_parameters[name].rvs())
            psoo.particles.append(Particle(np.array(startingPosition), psoo.minimize))
        psoo.neighbours = psoo.neighbourfunction(psoo.particles)
        psoo.learn()

        return co

    def log_optimized_info(self, optimized, csvScoresFilename=None):
        print("Best parameters set found on development set: %s", (optimized.best_estimator_,))
        print("Grid scores on development set:")
        sortedScores = sorted([(mean_score, scores.std() / 2, params, scores) for params, mean_score, scores in optimized.grid_scores_], reverse=True)
        for mean_score, std, params, scores in sortedScores:
            print("%0.3f (+/-%0.03f) for %r with %s" % (mean_score, std, params, scores))
        if csvScoresFilename is not None:
            with open(csvScoresFilename, 'wb') as f:
                writer = csv.writer(f)
                writer.writerows(sortedScores)

    def test_classifier(self, clf):
        testPredicted = clf.predict(self.testData)

        accuracy = metrics.accuracy_score(self.testLabel, testPredicted)
        # f1score = metrics.f1_score(self.testLabel, testPredicted, pos_label=None, average='weighted')

        p, r, f1, s = metrics.precision_recall_fscore_support(self.testLabel, testPredicted, average=None)

        p_wei_avg = np.average(p, weights=s)
        r_wei_avg = np.average(r, weights=s)
        f1_wei_avg = np.average(f1, weights=s)

        print 'Accuracy: ', accuracy
        print 'F1-score: ', f1_wei_avg

        self.evaluation = ClassifierEvaluation(
            self.name,
            self.optimizationMethod,
            self.optimized.best_params_,
            accuracy,
            f1_wei_avg, p_wei_avg, r_wei_avg,
            p[1], p[0],
            r[1], r[0],
            f1[1], f1[0],
            s[1], s[0]
        )

        print metrics.classification_report(self.testLabel, testPredicted)

        print self.evaluation
        return self.evaluation

    def run(self):
        self.optimized = self.algorithm()
        self.log_optimized_info(self.optimized, self.scoresCsvFilename)

        clf = self.optimized.best_estimator_
        evaluation = self.test_classifier(clf)

        if self.modelFilename:
            joblib.dump(clf, self.modelFilename)
        return evaluation

    def save_best_classifier_evaluation(self, filename, append=False):
        saveClassifiersEvaluations(filename, [self.evaluation], append)


if __name__ == '__main__':
    q = MetaOptimizer()