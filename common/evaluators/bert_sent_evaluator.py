import warnings

import numpy as np
import torch
import torch.nn.functional as F
from sklearn import metrics
from torch.utils.data import DataLoader, SequentialSampler, TensorDataset
from tqdm import tqdm

from datasets.bert_processors.abstract_processor import convert_examples_to_features_with_sentiment, \
    convert_examples_to_hierarchical_features
from utils.preprocessing import pad_input_matrix
from utils.tokenization import BertTokenizer

# Suppress warnings from sklearn.metrics
warnings.filterwarnings('ignore')


class BertEvaluator(object):
    def __init__(self, model, processor, args, split='dev'):
        self.args = args
        self.model = model
        self.processor = processor
        self.tokenizer = BertTokenizer.from_pretrained(args.model, is_lowercase=args.is_lowercase)
        if split == 'test':
            self.eval_examples = self.processor.get_test_examples(args.data_dir, args.test_name)
        elif split == 'dev':
            self.eval_examples = self.processor.get_dev_examples(args.data_dir, args.dev_name)
        else:
            self.eval_examples = self.processor.get_any_examples(args.data_dir, split)

    def get_scores(self, silent=False, return_indices=False):
        all_indices = []
        if self.args.is_hierarchical:
            eval_features = convert_examples_to_hierarchical_features(
                self.eval_examples, self.args.max_seq_length, self.tokenizer)
        else:
            eval_features = convert_examples_to_features_with_sentiment(
                self.eval_examples, self.args.max_seq_length, self.tokenizer, overal_sent=self.args.overal_sent)

        unpadded_input_ids = [f.input_ids for f in eval_features]
        unpadded_input_mask = [f.input_mask for f in eval_features]
        unpadded_segment_ids = [f.segment_ids for f in eval_features]
        unpadded_sent_scores = [f.sentiment_scores for f in eval_features]


        if self.args.is_hierarchical:
            pad_input_matrix(unpadded_input_ids, self.args.max_doc_length)
            pad_input_matrix(unpadded_input_mask, self.args.max_doc_length)
            pad_input_matrix(unpadded_segment_ids, self.args.max_doc_length)

        padded_input_ids = torch.tensor(unpadded_input_ids, dtype=torch.long)
        padded_input_mask = torch.tensor(unpadded_input_mask, dtype=torch.long)
        padded_segment_ids = torch.tensor(unpadded_segment_ids, dtype=torch.long)
        sent_scores = torch.tensor(unpadded_sent_scores, dtype=torch.long)
        label_ids = torch.tensor([f.label_id for f in eval_features], dtype=torch.long)

        eval_data = TensorDataset(padded_input_ids, padded_input_mask, padded_segment_ids, sent_scores, label_ids)
        eval_sampler = SequentialSampler(eval_data)
        eval_dataloader = DataLoader(eval_data, sampler=eval_sampler, batch_size=self.args.batch_size)

        self.model.eval()

        total_loss = 0
        nb_eval_steps, nb_eval_examples = 0, 0
        predicted_labels, target_labels = list(), list()

        for input_ids, input_mask, segment_ids, sent_scores, label_ids in tqdm(eval_dataloader, desc="Evaluating", disable=silent):
            input_ids = input_ids.to(self.args.device)
            input_mask = input_mask.to(self.args.device)
            segment_ids = segment_ids.to(self.args.device)
            sent_scores = sent_scores.to(self.args.device)
            label_ids = label_ids.to(self.args.device)

            with torch.no_grad():
                if return_indices:
                    outs = self.model(input_ids, segment_ids, input_mask, sent_scores=sent_scores, return_indices=return_indices)
                else:
                    outs = self.model(input_ids, segment_ids, input_mask, sent_scores=sent_scores)
                    if isinstance(outs, tuple):
                        outs, _ = outs

            if return_indices:
                logits, indices = outs
                all_indices.extend(indices.cpu().detach().numpy())
            else:
                logits = outs

            if self.args.is_multilabel:
                predicted_labels.extend(F.sigmoid(logits).round().long().cpu().detach().numpy())
                target_labels.extend(label_ids.cpu().detach().numpy())
                loss = F.binary_cross_entropy_with_logits(logits, label_ids.float(), size_average=False)
                average, average_mac = 'micro', 'macro'

            else:
                predicted_labels.extend(torch.argmax(logits, dim=1).cpu().detach().numpy())
                target_labels.extend(torch.argmax(label_ids, dim=1).cpu().detach().numpy())
                loss = F.cross_entropy(logits, torch.argmax(label_ids, dim=1))
                average, average_mac = 'binary', 'binary'

            if self.args.n_gpu > 1:
                loss = loss.mean()
            if self.args.gradient_accumulation_steps > 1:
                loss = loss / self.args.gradient_accumulation_steps
            total_loss += loss.item()

            nb_eval_examples += input_ids.size(0)
            nb_eval_steps += 1

        predicted_labels, target_labels = np.array(predicted_labels), np.array(target_labels)
        #accuracy = metrics.accuracy_score(target_labels, predicted_labels)
        accuracy = metrics.balanced_accuracy_score(target_labels, predicted_labels)
        precision = metrics.precision_score(target_labels, predicted_labels, average=average)
        recall = metrics.recall_score(target_labels, predicted_labels, average=average)
        avg_loss = total_loss / nb_eval_steps

        hamming_loss = metrics.hamming_loss(target_labels, predicted_labels)
        jaccard_score = metrics.jaccard_score(target_labels, predicted_labels, average=average)
        f1_micro = metrics.f1_score(target_labels, predicted_labels, average=average)
        f1_macro = metrics.f1_score(target_labels, predicted_labels, average=average_mac)

        if return_indices:
            return [accuracy, precision, recall, f1_micro, avg_loss, f1_macro, hamming_loss, jaccard_score, predicted_labels, target_labels, all_indices],\
               ['accuracy', 'precision', 'recall', 'f1_micro', 'avg_loss', 'f1_macro', 'hamming_loss', 'jaccard', 'predicted_labels', 'target_labels', 'all_indices']
        else:

            return [accuracy, precision, recall, f1_micro, avg_loss, f1_macro, hamming_loss, jaccard_score, predicted_labels, target_labels],\
               ['accuracy', 'precision', 'recall', 'f1_micro', 'avg_loss', 'f1_macro', 'hamming_loss', 'jaccard', 'predicted_labels', 'target_labels']





