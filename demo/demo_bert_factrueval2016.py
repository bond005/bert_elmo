from argparse import ArgumentParser
import codecs
import json
import logging
import os
import pickle
import random
import sys
import tempfile
from typing import Union

import numpy as np
from rusenttokenize import ru_sent_tokenize

try:
    from deep_ner.bert_ner import BERT_NER, bert_ner_logger
    from deep_ner.utils import factrueval2016_to_json, load_dataset_from_json, load_dataset_from_brat, set_total_seed
    from deep_ner.utils import divide_dataset_by_sentences
    from deep_ner.quality import calculate_prediction_quality
    from deep_ner.dataset_splitting import sample_from_dataset, split_dataset
except:
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    from deep_ner.bert_ner import BERT_NER, bert_ner_logger
    from deep_ner.utils import factrueval2016_to_json, load_dataset_from_json, load_dataset_from_brat, set_total_seed
    from deep_ner.utils import divide_dataset_by_sentences
    from deep_ner.quality import calculate_prediction_quality
    from deep_ner.dataset_splitting import sample_from_dataset, split_dataset


def train(factrueval2016_devset_dir: str, split_by_paragraphs: bool, bert_will_be_tuned: bool,
          use_lang_features: bool, use_shapes: bool, lstm_layer_size: Union[int, None], l2: float,
          max_epochs: int, patience: int, batch_size: int, gpu_memory_frac: float,
          model_name: str, collection3_dir: Union[str, None]=None, n_max_samples: int=0) -> BERT_NER:
    if os.path.isfile(model_name):
        with open(model_name, 'rb') as fp:
            recognizer = pickle.load(fp)
        assert isinstance(recognizer, BERT_NER)
        print('The NER has been successfully loaded from the file `{0}`...'.format(model_name))
        print('')
    else:
        temp_json_name = tempfile.NamedTemporaryFile(mode='w').name
        try:
            factrueval2016_to_json(factrueval2016_devset_dir, temp_json_name, split_by_paragraphs)
            X, y = load_dataset_from_json(temp_json_name)
        finally:
            if os.path.isfile(temp_json_name):
                os.remove(temp_json_name)
        print('The FactRuEval-2016 data for training have been loaded...')
        print('Number of samples is {0}.'.format(len(y)))
        indices_of_samples = sorted(
            list(range(len(y))),
            key=lambda sample_idx: (
                len(y[sample_idx]),
                sum(map(lambda it: len(y[sample_idx][it]), y[sample_idx]))
            )
        )
        for sample_idx in indices_of_samples[-3:]:
            print('')
            print('  {0}'.format(X[sample_idx]))
            for ne in sorted(list(y[sample_idx].keys())):
                print('  {0}:'.format(ne))
                print('    {0}'.format(y[sample_idx][ne]))
        del indices_of_samples
        print('')
        if BERT_NER.PATH_TO_BERT is None:
            bert_hub_module_handle = 'https://tfhub.dev/google/bert_multi_cased_L-12_H-768_A-12/1'
        else:
            bert_hub_module_handle = None
        recognizer = BERT_NER(
            finetune_bert=bert_will_be_tuned, batch_size=batch_size, l2_reg=l2,
            bert_hub_module_handle=bert_hub_module_handle, lstm_units=lstm_layer_size, validation_fraction=0.25,
            max_epochs=max_epochs, patience=patience, gpu_memory_frac=gpu_memory_frac, verbose=True, random_seed=42,
            lr=3e-6 if bert_will_be_tuned else 1e-4,
            udpipe_lang='ru', use_nlp_features=use_lang_features, use_shapes=use_shapes
        )
        train_index, test_index = split_dataset(y=y, test_part=recognizer.validation_fraction)
        X_train = np.array(X, dtype=object)[train_index]
        y_train = np.array(y, dtype=object)[train_index]
        X_val = np.array(X, dtype=object)[test_index]
        y_val = np.array(y, dtype=object)[test_index]
        del train_index, test_index
        if n_max_samples > 0:
            index = sample_from_dataset(y=y_train, n=n_max_samples)
            X_train = X_train[index]
            y_train = y_train[index]
            del index
        if collection3_dir is not None:
            X_train_, y_train_ = load_dataset_from_brat(collection3_dir, split_by_paragraphs=True)
            if not split_by_paragraphs:
                X_train_, y_train_ = divide_dataset_by_sentences(X_train_, y_train_,
                                                                 sent_tokenize_func=ru_sent_tokenize)
            for sample_idx in range(len(y_train_)):
                new_y_sample = dict()
                for ne_type in sorted(list(y_train_[sample_idx].keys())):
                    if ne_type == 'PER':
                        new_y_sample['PERSON'] = y_train_[sample_idx][ne_type]
                    elif ne_type == 'LOC':
                        new_y_sample['LOCATION'] = y_train_[sample_idx][ne_type]
                    else:
                        new_y_sample[ne_type] = y_train_[sample_idx][ne_type]
                y_train_[sample_idx] = new_y_sample
                del new_y_sample
            print('The Collection3 data for training have been loaded...')
            print('Number of samples is {0}.'.format(len(y_train)))
            indices_of_samples = sorted(
                list(range(len(y_train_))),
                key=lambda sample_idx: (
                    len(y_train_[sample_idx]),
                    sum(map(lambda it: len(y_train_[sample_idx][it]), y_train_[sample_idx]))
                )
            )
            for sample_idx in indices_of_samples[-3:]:
                print('')
                print('  {0}'.format(X_train_[sample_idx]))
                for ne in sorted(list(y_train_[sample_idx].keys())):
                    print('  {0}:'.format(ne))
                    print('    {0}'.format(y_train_[sample_idx][ne]))
            del indices_of_samples
            print('')
            if (n_max_samples == 0) or ((n_max_samples > 0) and (X_train.shape[0] < n_max_samples)):
                if n_max_samples > 0:
                    index = sample_from_dataset(y=y_train, n=n_max_samples - X_train.shape[0])
                    X_train_ = np.array(X_train_, dtype=object)[index]
                    y_train_ = np.array(y_train_, dtype=object)[index]
                    del index
                X_train = np.concatenate((X_train, np.array(X_train_, dtype=object)))
                y_train = np.concatenate((y_train, np.array(y_train_, dtype=object)))
            del X_train_, y_train_
        recognizer.fit(X_train, y_train, validation_data=(X_val, y_val))
        with open(model_name, 'wb') as fp:
            pickle.dump(recognizer, fp)
        print('')
        print('The NER has been successfully fitted and saved into the file `{0}`...'.format(model_name))
        print('')
    return recognizer


def recognize(factrueval2016_testset_dir: str, split_by_paragraphs: bool, recognizer: BERT_NER, results_dir: str):
    temp_json_name = tempfile.NamedTemporaryFile(mode='w').name
    try:
        factrueval2016_to_json(factrueval2016_testset_dir, temp_json_name, split_by_paragraphs)
        with codecs.open(temp_json_name, mode='r', encoding='utf-8', errors='ignore') as fp:
            data_for_testing = json.load(fp)
        _, true_entities = load_dataset_from_json(temp_json_name)
    finally:
        if os.path.isfile(temp_json_name):
            os.remove(temp_json_name)
    texts = []
    additional_info = []
    for cur_document in data_for_testing:
        base_name = os.path.join(results_dir, cur_document['base_name'] + '.task1')
        for cur_paragraph in cur_document['paragraph_bounds']:
            texts.append(cur_document['text'][cur_paragraph[0]:cur_paragraph[1]])
            additional_info.append((base_name, cur_paragraph))
    print('Data for final testing have been loaded...')
    print('Number of samples is {0}.'.format(len(true_entities)))
    print('')
    predicted_entities = recognizer.predict(texts)
    assert len(predicted_entities) == len(true_entities)
    f1, precision, recall, quality_by_entities = calculate_prediction_quality(
        true_entities, predicted_entities, recognizer.classes_list_)
    print('All entities:')
    print('    F1-score is {0:.2%}.'.format(f1))
    print('    Precision is {0:.2%}.'.format(precision))
    print('    Recall is {0:.2%}.'.format(recall))
    for ne_type in sorted(list(quality_by_entities.keys())):
        print('  {0}'.format(ne_type))
        print('    F1-score is {0:.2%}.'.format(quality_by_entities[ne_type][0]))
        print('    Precision is {0:.2%}.'.format(quality_by_entities[ne_type][1]))
        print('    Recall is {0:.2%}.'.format(quality_by_entities[ne_type][2]))
    results_for_factrueval_2016 = dict()
    for sample_idx, cur_result in enumerate(predicted_entities):
        base_name, paragraph_bounds = additional_info[sample_idx]
        for entity_type in cur_result:
            if entity_type == 'ORG':
                prepared_entity_type = 'org'
            elif entity_type == 'PERSON':
                prepared_entity_type = 'per'
            elif entity_type == 'LOCATION':
                prepared_entity_type = 'loc'
            else:
                prepared_entity_type = None
            if prepared_entity_type is None:
                raise ValueError('`{0}` is unknown entity type!'.format(entity_type))
            for entity_bounds in cur_result[entity_type]:
                postprocessed_entity = (
                    prepared_entity_type,
                    entity_bounds[0] + paragraph_bounds[0],
                    entity_bounds[1] - entity_bounds[0]
                )
                if base_name in results_for_factrueval_2016:
                    results_for_factrueval_2016[base_name].append(postprocessed_entity)
                else:
                    results_for_factrueval_2016[base_name] = [postprocessed_entity]
    for base_name in results_for_factrueval_2016:
        with codecs.open(base_name, mode='w', encoding='utf-8', errors='ignore') as fp:
            for cur_entity in sorted(results_for_factrueval_2016[base_name], key=lambda it: (it[1], it[2], it[0])):
                fp.write('{0} {1} {2}\n'.format(cur_entity[0], cur_entity[1], cur_entity[2]))


def main():
    parser = ArgumentParser()
    parser.add_argument('-m', '--model', dest='model_name', type=str, required=True,
                        help='The binary file with the NER model.')
    parser.add_argument('-d', '--data', dest='data_name', type=str, required=True,
                        help='Path to the FactRuEval-2016 repository.')
    parser.add_argument('-n', '--number', dest='samples_number', type=int, required=False, default=None,
                        help='Number of samples of the training sub-set.')
    parser.add_argument('-r', '--result', dest='result_name', type=str, required=True,
                        help='The directory into which all recognized named entity labels will be saved.')
    parser.add_argument('-c', '--collection', dest='collection_data_name', type=str, required=False, default=None,
                        help='Path to the Collection-3 data set.')
    parser.add_argument('--batch', dest='batch_size', type=int, required=False, default=16,
                        help='Size of mini-batch.')
    parser.add_argument('--max_epochs', dest='max_epochs', type=int, required=False, default=100,
                        help='Maximal number of training epochs.')
    parser.add_argument('--patience', dest='patience', type=int, required=False, default=10,
                        help='Number of iterations with no improvement to wait before stopping the training.')
    parser.add_argument('--lstm', dest='lstm_units', type=int, required=False, default=None,
                        help='The LSTM layer size (if it is not specified, than the LSTM layer is not used).')
    parser.add_argument('--l2', dest='l2_coeff', type=float, required=False, default=1e-2,
                        help='L2 regularization factor.')
    parser.add_argument('--gpu_frac', dest='gpu_memory_frac', type=float, required=False, default=0.9,
                        help='Allocable part of the GPU memory for the NER model.')
    parser.add_argument('--finetune_bert', dest='finetune_bert', required=False, action='store_true',
                        default=False, help='Will be the BERT and last layers finetuned together? Or the BERT will be frozen?')
    parser.add_argument('--path_to_bert', dest='path_to_bert', required=False, type=str,
                        default=None, help='Path to the BERT model (if it is not specified, than the standard '
                                           'multilingual BERT model from the TF-Hub will be used).')
    parser.add_argument('--text', dest='text_unit', type=str, choices=['sentence', 'paragraph'], required=False,
                        default='sentence', help='Text unit: sentence or paragraph.')
    parser.add_argument('--lang_features', dest='lang_features', required=False, action='store_true',
                        default=False, help='Will be morphology and syntax used as additional feautres?')
    parser.add_argument('--shapes', dest='shapes', required=False, action='store_true',
                        default=False, help='Will be word shapes used as additional features?')
    parser.add_argument('--seed', dest='random_seed', type=int, required=False, default=None,
                        help='The random seed.')
    args = parser.parse_args()

    if args.random_seed is not None:
        random.seed(args.random_seed)

    if args.text_unit not in {'sentence', 'paragraph'}:
        raise ValueError('`{0}` is wrong value for the `text_unit` parameter!'.format(args.text_unit))
    if args.path_to_bert is None:
        path_to_bert = None
    else:
        path_to_bert = os.path.normpath(args.path_to_bert)
        if len(path_to_bert) == 0:
            raise ValueError('The BERT model cannot be contained into the current directory!')
        if not os.path.isdir(path_to_bert):
            raise ValueError('The directory `{0}` does not exist!'.format(path_to_bert))
    BERT_NER.PATH_TO_BERT = path_to_bert
    collection3_dir_name = None if args.collection_data_name is None else os.path.normpath(args.collection_data_name)
    devset_dir_name = os.path.join(os.path.normpath(args.data_name), 'devset')
    testset_dir_name = os.path.join(os.path.normpath(args.data_name), 'testset')
    if args.random_seed is not None:
        set_total_seed(args.random_seed)
    if args.samples_number is None:
        samples_number = 0
    else:
        samples_number = args.samples_number
        if samples_number < 1:
            raise ValueError('The samples number in training sub-set is wrong! It must be a positive integer value.')
    recognizer = train(factrueval2016_devset_dir=devset_dir_name, bert_will_be_tuned=args.finetune_bert,
                       use_lang_features=args.lang_features, use_shapes=args.shapes, max_epochs=args.max_epochs,
                       patience=args.patience, batch_size=args.batch_size, gpu_memory_frac=args.gpu_memory_frac,
                       model_name=os.path.normpath(args.model_name), lstm_layer_size=args.lstm_units, l2=args.l2_coeff,
                       split_by_paragraphs=(args.text_unit == 'paragraph'), collection3_dir=collection3_dir_name,
                       n_max_samples=samples_number)
    recognize(factrueval2016_testset_dir=testset_dir_name, recognizer=recognizer,
              results_dir=os.path.normpath(args.result_name), split_by_paragraphs=(args.text_unit == 'paragraph'))


if __name__ == '__main__':
    bert_ner_logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(filename)s[LINE:%(lineno)d]# %(levelname)-8s [%(asctime)s]  %(message)s')
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    bert_ner_logger.addHandler(handler)
    main()
