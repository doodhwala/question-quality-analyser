import csv
import dill
import numpy as np
import pickle
import pprint
import sys
import os

from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, classification_report, confusion_matrix
from utils import clean_no_stopwords
from utils import get_data_for_cognitive_classifiers

sys.setrecursionlimit(2 * 10 ** 7)


def sent_to_glove(questions, w2v):
	questions_w2glove = []
	
	for question in questions:
		vec = []
		for word in question:
			if word in w2v:
				vec.append(w2v[word])
			else:
				vec.append(np.zeros(len(w2v['the'])))
		questions_w2glove.append(np.array(vec))
	
	return np.array(questions_w2glove)

def relu(z):
	y = z * (z > 0)
	return np.clip(y, 0, 10)

def relu_prime(z):
	return (z > 0)

def clip(v):
		return v[:10]

class RNN:
	def __init__(self, input_size, hidden_size, output_size, learning_rate=0.01, direction="right"):
		self.direction = direction
		self.hidden_size 	= hidden_size
		self.learning_rate = learning_rate
		self.f = relu #np.tanh
		self.f_prime = relu_prime #lambda x: 1 - (x ** 2)

		self.Wxh = np.random.randn(hidden_size, input_size) * np.sqrt(2.0 / (hidden_size + input_size))
		self.Whh = np.random.randn(hidden_size, hidden_size) * np.sqrt(2.0 / (hidden_size * 2))
		self.Why = np.random.randn(output_size, hidden_size) * np.sqrt(2.0 / (hidden_size + output_size))
		self.bh = np.zeros((hidden_size, 1))
		self.by = np.zeros((output_size, 1)) # output bias - computed but not used

		self.mWxh, self.mWhh, self.mWhy = np.zeros_like(self.Wxh), np.zeros_like(self.Whh), np.zeros_like(self.Why)
		self.mbh, self.mby = np.zeros_like(self.bh), np.zeros_like(self.by) # memory variables for Adagrad
		
		self.dropout_percent = 0.2

	def forward(self, x, hprev, do_dropout=False):
		if(self.direction == 'left'):
			x = x[::-1]
	
		xs, hs, ys, ps = {}, {}, {}, {}
		hs[-1] = np.copy(hprev)

		seq_length = len(x)

		for t in range(seq_length):
			xs[t] = x[t].reshape(-1, 1)
			hs[t] = self.f(np.dot(self.Wxh, xs[t]) + np.dot(self.Whh, hs[t-1]) + self.bh)
			
			if(do_dropout):
				hs[t] *= np.random.binomial(1, 1 - self.dropout_percent, size=hs[t-1].shape)
			
			ys[t] = self.f(np.dot(self.Why, hs[t]) + self.by)
			ps[t] = np.exp(ys[t]) / np.sum(np.exp(ys[t]))
		return xs, hs, ys, ps

	def backprop(self, xs, hs, ys, ps, targets, dy, do_dropout=False):
		if(self.direction == 'left'):
			xs = {len(xs) - 1 - k : xs[k] for k in xs}

		dWxh, dWhh, dWhy = np.zeros_like(self.Wxh), np.zeros_like(self.Whh), np.zeros_like(self.Why)
		dbh, dby = np.zeros_like(self.bh), np.zeros_like(self.by)
		dhnext = np.zeros_like(hs[-1])

		for t in reversed(range(len(xs))):
			tmp = dy[t] * self.f_prime(ys[t])
			dWhy += np.dot(tmp, hs[t].T)
			dby += tmp
			dh = np.dot(self.Why.T, dy[t]) + dhnext
			dhraw = dh * self.f_prime(hs[t])
			dbh += dhraw
			dWxh += np.dot(dhraw, xs[t].T)
			dWhh += np.dot(dhraw, hs[t-1].T)
			dhnext = np.dot(self.Whh.T, dhraw)

		for dparam in [dWxh, dWhh, dWhy, dbh, dby]:
			np.clip(dparam, -5, 5, out=dparam) # clip to mitigate exploding gradients

		return dWxh, dWhh, dWhy, dbh, dby, hs[len(xs) - 1]

	def update_params(self, dWxh, dWhh, dWhy, dbh, dby):
		# perform parameter update with Adagrad
		for param, dparam, mem in zip([self.Wxh, self.Whh, self.Why, self.bh, self.by],
		                            [dWxh, dWhh, dWhy, dbh, dby],
		                            [self.mWxh, self.mWhh, self.mWhy, self.mbh, self.mby]):
			mem += dparam * dparam
			param += -self.learning_rate * dparam / np.sqrt(mem + 1e-8) # adagrad update

class BiDirectionalRNN:
	def __init__(self, input_size, hidden_size, output_size, learning_rate=0.01):
		self.hidden_size = hidden_size
		self.learning_rate = learning_rate
		
		self.right = RNN(input_size, hidden_size, output_size, learning_rate, direction="right")
		self.left = RNN(input_size, hidden_size, output_size, learning_rate, direction="left")
		
		self.by = np.zeros((output_size, 1))
		self.mby = np.zeros_like(self.by)
	
	def forward(self, x):
		seq_length = len(x)
		
		y_pred = []
		dby = np.zeros_like(self.by)
		xsl, hsl, ysl, psl = self.left.forward(x, np.zeros((self.hidden_size, 1)))
		xsr, hsr, ysr, psr = self.right.forward(x, np.zeros((self.hidden_size, 1)))
		
		for ind in range(seq_length):
			this_y = np.dot(self.right.Why, hsr[ind]) + np.dot(self.left.Why, hsl[ind]) + self.by
			y_pred.append(this_y)
		
		return np.argmax(y_pred[-1])
	
	def train(self, training_data, validation_data, epochs=5, do_dropout=False):
		for e in range(epochs):
			print('Epoch {}'.format(e + 1))
			
			for x, y in zip(*training_data):
				x = clip(x)
				
				hprevr = np.zeros((self.hidden_size, 1))
				hprevl = np.zeros((self.hidden_size, 1))
				
				seq_length = len(x)
				
				xsl, hsl, ysl, psl = self.left.forward(x, hprevl, do_dropout)
				xsr, hsr, ysr, psr = self.right.forward(x, hprevr, do_dropout)
				
				y_pred = []
				dy = []
				dby = np.zeros_like(self.by)
				for ind in range(seq_length):
					this_y = np.dot(self.right.Why, hsr[ind]) + np.dot(self.left.Why, hsl[ind]) + self.by
					y_pred.append(this_y)
				
				for ind in range(seq_length):
					this_dy = np.exp(y_pred[ind]) / np.sum(np.exp(y_pred[ind]))
					t = np.argmax(y)
					this_dy[t] -= 1
					dy.append(this_dy)
					dby += this_dy
				
				y_pred = np.array(y_pred)
				dy = np.array(dy)
				
				self.mby += dby * dby
				self.by += -self.learning_rate * dby / np.sqrt(self.mby + 1e-8) # adagrad update
				
				dWxhr, dWhhr, dWhyr, dbhr, dbyr, hprevr = self.right.backprop(xsr, hsr, ysr, psr, y, dy, do_dropout)
				dWxhl, dWhhl, dWhyl, dbhl, dbyl, hprevl = self.left.backprop(xsl, hsl, ysl, psl, y, dy, do_dropout)
				
				self.right.update_params(dWxhr, dWhhr, dWhyr, dbhr, dbyr)
				self.left.update_params(dWxhl, dWhhl, dWhyl, dbhl, dbyl)
			
			self.predict(validation_data)

		save_model(self)
		print("\nTraining done.")
	
	def predict(self, testing_data, test=False):
		targets = []
		predictions = []
		for x, y in zip(*testing_data):
			x = clip(x)
			tr = np.argmax(y)
			op = self.forward(x)
			targets.append(tr)
			predictions.append(op)
		
		if not test:
			print('[val acc:      {:.2f}%]'.format(accuracy_score(targets, predictions) * 100))
			print('[val f1 score: {:.2f}]'.format(f1_score(targets, predictions, average="macro")))
			'''
			print(precision_score(targets, predictions, average="macro"))
			print(recall_score(targets, predictions, average="macro"))
			print(confusion_matrix(targets, predictions))
			'''
		else:
			print(classification_report(targets, predictions))

		return accuracy_score(targets, predictions)
		
		

def save_model(clf):
	with open('models/brnn_model.pkl', 'wb') as f:
		dill.dump(clf, f)

def load_model():
	with open('models/brnn_model.pkl', 'rb') as f:
		clf = dill.load(f)
	return clf

if __name__ == "__main__":
	NUM_CLASSES = 6
	INPUT_SIZE = 300
	
	CURSOR_UP_ONE = '\x1b[1A'
	ERASE_LINE = '\x1b[2K'
	
	X_data = []
	Y_data = []
	
	X_train, Y_train, X_test, Y_test = get_data_for_cognitive_classifiers(threshold=[0.1, 0.15, 0.2, 0.25], what_type=['ada', 'bcl', 'os'], split=0.8, include_keywords=True, keep_dup=False)
	
	X_data = X_train + X_test
	Y_data = Y_train + Y_test
	
	vocabulary = {'the'}
	
	for x in X_train + X_test:
	    vocabulary = vocabulary.union(set(x))
	
	filename = 'glove.840B.%dd.txt' %INPUT_SIZE
	
	if not os.path.exists('models/%s_saved.pkl' %filename.split('.txt')[0]):
		print()
		with open('models/' + filename, "r", encoding='utf-8') as lines:
		    w2v = {}
		    for row, line in enumerate(lines):
		        try:
		            w = line.split()[0]
		            if w not in vocabulary:
		            	continue
		            vec = np.array(list(map(float, line.split()[1:])))
		            w2v[w] = vec
		        except:
		            continue
		        finally:
		            print(CURSOR_UP_ONE + ERASE_LINE + 'Processed {} GloVe vectors'.format(row + 1))
		
		dill.dump(w2v, open('models/%s_saved.pkl' %filename.split('.txt')[0], 'wb'))
	else:
		w2v = dill.load(open('models/%s_saved.pkl' %filename.split('.txt')[0], 'rb'))
	
	X_data = sent_to_glove(X_data, w2v)
	
	for i in range(len(Y_data)):
		v = np.zeros(NUM_CLASSES)
		v[Y_data[i]] = 1
		Y_data[i] = v
	
	Y_data = np.array(Y_data)
	
	X_train = np.array(X_data[: int(len(X_data) * 0.75) ])
	Y_train = np.array(Y_data[: int(len(X_data) * 0.75) ])
	
	X_val = np.array(X_data[int(len(X_data) * 0.75) : int(len(X_data) * 0.80)])
	Y_val = np.array(Y_data[int(len(X_data) * 0.75) : int(len(X_data) * 0.80)])
	
	X_test = np.array(X_data[int(len(X_data) * 0.80) :])
	Y_test = np.array(Y_data[int(len(X_data) * 0.80) :])
	
	print('Data Loaded/Preprocessed')
	
	HIDDEN_SIZE = 128
	OUTPUT_SIZE = NUM_CLASSES
	
	EPOCHS = 5
	LEARNING_RATE = 0.010
	
	TRAIN = True
	RETRAIN = False
	
	BRNN = None
	if TRAIN:
		if(RETRAIN):
			BRNN = load_model()
		else:
			BRNN = BiDirectionalRNN(INPUT_SIZE, HIDDEN_SIZE, OUTPUT_SIZE, learning_rate=LEARNING_RATE)
		BRNN.train(training_data=(X_train, Y_train), validation_data=(X_val, Y_val), epochs=EPOCHS, do_dropout=False)
		save_model(BRNN)
	else:
		BRNN = load_model()
	
	print()
	accuracy = BRNN.predict((X_test, Y_test), True)
	
	print("Accuracy: {:.2f}%".format(accuracy * 100))