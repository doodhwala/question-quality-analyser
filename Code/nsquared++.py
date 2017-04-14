import platform
import re
import os
import pickle
import csv
import numpy as np
import pprint
import sys

import gensim
from gensim import corpora, models, similarities
from gensim.matutils import cossim, sparse2full

import nltk
from nltk import stem
from nltk.stem.wordnet import WordNetLemmatizer
from nltk.tokenize import wordpunct_tokenize
from nltk.corpus import stopwords as stp

from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import accuracy_score
from sklearn.externals import joblib

from nsquared import DocumentClassifier
from utils import get_knowledge_probs, get_data_for_knowledge_classifiers



import logging
logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)

stemmer = stem.porter.PorterStemmer()
wordnet = WordNetLemmatizer()

if len(sys.argv) < 2:
    subject = 'ADA'
else:
	subject = sys.argv[1]


CURSOR_UP_ONE = '\x1b[1A'
ERASE_LINE = '\x1b[2K'

knowledge_mapping = {'Metacognitive': 3, 'Procedural': 2, 'Conceptual': 1, 'Factual': 0}

skip_files={'__', '.DS_Store', 'Key Terms, Review Questions, and Problems', 'Recommended Reading and Web Sites', 'Recommended Reading', 'Summary', 'Exercises', 'Introduction'}

punkt = {',', '"', "'", ':', ';', '(', ')', '[', ']', '{', '}', '.', '?', '!', '`', '|', '-', '=', '+', '_', '>', '<'}

if(platform.system() == 'Windows'):
	stopwords = set(re.split(r'[\s]', re.sub('[\W]', '', open('resources/stopwords.txt', 'r', encoding='utf8').read().lower(), re.M), flags=re.M) + [chr(i) for i in range(ord('a'), ord('z') + 1)])
else:
	stopwords = set(re.split(r'[\s]', re.sub('[\W]', '', open('resources/stopwords.txt', 'r').read().lower(), re.M), flags=re.M) + [chr(i) for i in range(ord('a'), ord('z') + 1)])
stopwords.update(punkt)

stopwords2 = stp.words('english')

def __preprocess(text, stop_strength=0, remove_punct=True):
	text = re.sub('-\n', '', text).lower()
	text = re.sub('\n', ' ', text)
	if remove_punct:
		text = re.sub('[^a-z ]', '', text)
	else:
		text = re.sub('[^a-z.?! ]', '', text)

	tokens = [word for word in text.split()]  
	if stop_strength == 0:
		return ' '.join([wordnet.lemmatize(i) for i in tokens])
	elif stop_strength == 1:
		return ' '.join([wordnet.lemmatize(i) for i in tokens if i not in stopwords2])
	else:
		return ' '.join([wordnet.lemmatize(i) for i in tokens if i not in stopwords])


docs = {}
for file_name in sorted(os.listdir('resources/%s' % (subject, ))):
			with open(os.path.join('resources', subject, file_name), encoding='latin-1') as f:
				content = f.read() #re.split('\n[\s]*Exercise', f.read())[0] 
				title = content.split('\n')[0]
				if len([1 for k in skip_files if (k in title or k in file_name)]):
					continue
				docs[title] = __preprocess(content, stop_strength=2, remove_punct=False)

doc_set = list(docs.values())

texts = []
for i in doc_set:
    texts.append(__preprocess(i, stop_strength=1).split())

MODEL = ['LDA', 'LSA', 'D2V']

USE_MODELS = MODEL[0:3]

if MODEL[0] in USE_MODELS:
	TRAIN_LDA = False

	if TRAIN_LDA:
		dictionary = corpora.Dictionary(texts)
		dictionary.save('models/Nsquared/%s/dictionary.dict' % (subject, ))

		corpus = []
		for k in docs:
			corpus.extend([dictionary.doc2bow(sentence.split()) for sentence in nltk.sent_tokenize(docs[k]) if len(sentence.split()) > 2])
		corpora.MmCorpus.serialize('models/Nsquared/%s/corpus.mm' % (subject, ), corpus)

		lda = models.LdaModel(corpus=gensim.utils.RepeatCorpus(corpus, 5000),
	                          id2word=dictionary,
	                          num_topics=len(docs),
	                          update_every=1,
	                          passes=2)
		lda.save('models/Nsquared/%s/lda.model' % (subject, ))

		print('Model training done')

	else:
	    dictionary = corpora.Dictionary.load('models/Nsquared/%s/dictionary.dict' % (subject, ))
	    corpus = corpora.MmCorpus('models/Nsquared/%s/corpus.mm' % (subject, ))
	    lda = models.LdaModel.load('models/Nsquared/%s/lda.model' % (subject, ))

if MODEL[1] in USE_MODELS:
	TRAIN_LSA = False

	if TRAIN_LSA:
		dictionary = corpora.Dictionary(texts)
		dictionary.save('models/Nsquared/%s/dictionary.dict' %subject)  # store the dictionary, for future reference
		
		corpus = []
		for k in docs:
			corpus.extend([dictionary.doc2bow(sentence.split()) for sentence in nltk.sent_tokenize(docs[k])])
		corpora.MmCorpus.serialize('models/Nsquared/%s/corpus.mm' %subject, corpus)  # store to disk, for later use

		tfidf_model = models.TfidfModel(corpus, id2word=dictionary, normalize=True)
		tfidf_model.save('models/Nsquared/%s/tfidf.model' %subject)

		lsi_model = models.LsiModel(corpus=gensim.utils.RepeatCorpus(tfidf_model[corpus], 5000), 
									id2word=dictionary,
									num_topics=len(docs),
									onepass=False,
									power_iters=2,
									extra_samples=300)
		lsi_model.save('models/Nsquared/%s/lsi.model' %subject)

		print('Model training done')
	else:
		dictionary = corpora.Dictionary.load('models/Nsquared/%s/dictionary.dict' %subject)
		corpus = corpora.MmCorpus("models/Nsquared/%s/corpus.mm" %subject)
		tfidf_model = models.TfidfModel.load('models/Nsquared/%s/tfidf.model' %subject)
		lsi_model = models.LsiModel.load('models/Nsquared/%s/lsi.model' %subject)

	index = similarities.MatrixSimilarity(lsi_model[tfidf_model[corpus]], num_features=lsi_model.num_topics)
	index.save('models/Nsquared/%s/lsi.index' %subject)

if MODEL[2] in USE_MODELS:

	TRAIN_D2V = False

	if TRAIN_D2V:
		x_train = []
		for i, k in enumerate(docs):
			x_train.append(models.doc2vec.LabeledSentence(docs[k].split(), [k]))

		d2v_model = models.doc2vec.Doc2Vec(size=64, alpha=0.025, min_alpha=0.025, window=2, min_count=3, dbow_words=1, workers=4)  # use fixed learning rate
		d2v_model.build_vocab(x_train)
		for epoch in range(10):
		    d2v_model.train(x_train)
		    d2v_model.alpha -= 0.002  
		    d2v_model.min_alpha = d2v_model.alpha  

		d2v_model.save('models/Nsquared/%s/d2v.model' %subject)
	else:
		d2v_model = models.doc2vec.Doc2Vec.load('models/Nsquared/%s/d2v.model' %subject)

if subject == 'ADA':
	clf = pickle.load(open('models/Nsquared/%s/nsquared_92.pkl' % (subject, ), 'rb'))
else:
	clf = pickle.load(open('models/Nsquared/%s/nsquared_94.pkl' % (subject, ), 'rb'))	

x_data, y_data = get_data_for_knowledge_classifiers(subject)

print()
y_probs = []
nCorrect = nTotal = 0
for x, y in zip(x_data, y_data):
	print(CURSOR_UP_ONE + ERASE_LINE + '[N-Squared] Testing For', (nTotal + 1))
	c, k = clf.classify([x])[0]
	cleaned_question = __preprocess(x, stop_strength=1, remove_punct=False)

	p_list = []
	if MODEL[0] in USE_MODELS:
		s1 = lda[dictionary.doc2bow(docs[c].split())]
		s2 = lda[dictionary.doc2bow(cleaned_question.split())]
		d1 = sparse2full(s1, lda.num_topics)
		d2 = sparse2full(s2, lda.num_topics)
		lda_p = cossim(s1, s2)
		p_list.append(lda_p)
	
	if MODEL[1] in USE_MODELS:
		s1 = lsi_model[tfidf_model[dictionary.doc2bow(docs[c].split())]]
		s2 = lsi_model[tfidf_model[dictionary.doc2bow(cleaned_question.split() )]]
		lsa_p = cossim(s1, s2)
		p_list.append(lsa_p)
	
	if MODEL[2] in USE_MODELS:
		d1 = np.mean([d2v_model.infer_vector(s.split()) for s in nltk.sent_tokenize(docs[c])], axis=0).reshape(1, -1)
		d2 = d2v_model.infer_vector(cleaned_question.split()).reshape(1, -1)
		d2v_p = cosine_similarity(d1, d2)[0][0]
		p_list.append(d2v_p)

	p_list.append(k)
	y_pred = np.argmax(get_knowledge_probs(abs(p_list[0])))
	y_probs.append(p_list)
	if y_pred == y:
		nCorrect += 1
	nTotal += 1

print('Accuracy: {:.2f}%'.format(nCorrect / nTotal * 100))

x_train, x_test, y_train, y_test = train_test_split(y_probs, y_data, test_size=0.20)

###### NEURAL NETWORK BASED SIMVAL -> KNOW MAPPING ########
ann_clf = MLPClassifier(solver='adam', alpha=1e-3, hidden_layer_sizes=(32, 16), batch_size=4, learning_rate='adaptive', learning_rate_init=0.001, verbose=True)
ann_clf.fit(x_train, y_train)
print('ANN training completed')
y_real, y_pred = y_test, ann_clf.predict(x_test)

print('Accuracy: {:.2f}%'.format(accuracy_score(y_real, y_pred) * 100))

joblib.dump(ann_clf, 'models/Nsquared/%s/know_ann_clf.pkl' %subject)
