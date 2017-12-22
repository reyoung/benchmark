import paddle.v2.fluid as fluid
import paddle.v2.dataset.imdb as imdb
import paddle.v2.reader as reader
from paddle.v2 import batch
import os
import argparse
import cPickle
import numpy
import copy

try:
    with open('word_dict.pkl', 'r') as f:
        word_dict = cPickle.load(f)
except:
    word_dict = imdb.word_dict()
    with open('word_dict.pkl', 'w') as f:
        cPickle.dump(word_dict, f, cPickle.HIGHEST_PROTOCOL)


def cache_reader(reader):
    print 'Reading data to memory'
    try:
        with open('data.pkl', 'r') as f:
            items = cPickle.load(f)
    except:
        items = list(reader())
        with open('data.pkl', 'w') as f:
            cPickle.dump(items, f, cPickle.HIGHEST_PROTOCOL)
    print 'Done.'

    def __impl__():
        for item in items:
            yield item

    return __impl__


def crop_sentence(reader, crop_size):
    unk_value = word_dict['<unk>']

    def __impl__():
        for item in reader():
            if len([x for x in item[0] if x != unk_value]) < crop_size:
                yield item

    return __impl__


def main():
    args = parse_args()
    data = fluid.layers.data(
        name="words", shape=[1], lod_level=1, dtype='int64')
    clip_grad = fluid.ParamAttr(clip=fluid.clip.GradientClipByValue(1.0))
    sentence = fluid.layers.embedding(
        input=data,
        size=[len(word_dict), args.emb_dim],
        param_attr=copy.deepcopy(clip_grad))
    rnn = fluid.layers.DynamicRNN()
    with rnn.block():
        word = rnn.step_input(sentence)
        ex_state = rnn.memory(value=0.0, shape=[100])
        hidden = fluid.layers.fc(input=[word, ex_state],
                                 size=100,
                                 act='tanh',
                                 param_attr=copy.deepcopy(clip_grad),
                                 bias_attr=copy.deepcopy(clip_grad))
        rnn.update_memory(ex_state, hidden)
        rnn.output(hidden)

    last = fluid.layers.sequence_pool(rnn(), 'last')
    logit = fluid.layers.fc(input=last, size=2, act='softmax')
    loss = fluid.layers.cross_entropy(
        input=logit,
        label=fluid.layers.data(
            name='label', shape=[1], dtype='int64'))
    loss = fluid.layers.mean(x=loss)

    adam = fluid.optimizer.Adam()
    adam.minimize(loss)

    place = fluid.CPUPlace() if args.device == 'CPU' else fluid.GPUPlace(0)
    exe = fluid.Executor(place)
    exe.run(fluid.default_startup_program())
    train = cache_reader(imdb.train(word_dict))

    def train_loop(pass_num, crop_size):
        for pass_id in range(pass_num):
            train_reader = batch(
                crop_sentence(train, crop_size), batch_size=args.batch_size)
            for batch_id, data in enumerate(train_reader()):
                tensor_words = to_lodtensor([x[0] for x in data], place)
                label = numpy.array([x[1] for x in data]).astype("int64")
                label = label.reshape((-1, 1))
                loss_np = exe.run(fluid.default_main_program(),
                                  feed={"words": tensor_words,
                                        "label": label},
                                  fetch_list=[loss])[0]
                print 'Pass', pass_id, 'Batch', batch_id, 'loss', loss_np
            print 'Pass', pass_id, 'Done'

    train_loop(args.pass_num, 25)


def parse_args():
    parser = argparse.ArgumentParser("Understand Sentiment by Dynamic RNN.")
    parser.add_argument(
        '--batch_size',
        type=int,
        default=int(os.environ.get('BATCH_SIZE', '32')),
        help='The minibatch size.')
    parser.add_argument(
        '--emb_dim',
        type=int,
        default=int(os.environ.get('EMB_DIM', '32')),
        help='The embedding dim.')
    parser.add_argument(
        '--pass_num',
        type=int,
        default=int(os.environ.get('PASS_NUM', '100')),
        help='The number of passes.')
    parser.add_argument(
        '--device',
        type=str,
        default='CPU',
        choices=['CPU', 'GPU'],
        help='The device type.')
    parser.add_argument(
        '--crop_size',
        type=int,
        default=int(os.environ.get('CROP_SIZE', '25')),
        help='The max sentence length of input. Since this model use plain RNN,'
        ' Gradient could be explored if sentence is too long')
    args = parser.parse_args()
    return args


def to_lodtensor(data, place):
    seq_lens = [len(seq) for seq in data]
    cur_len = 0
    lod = [cur_len]
    for l in seq_lens:
        cur_len += l
        lod.append(cur_len)
    flattened_data = numpy.concatenate(data, axis=0).astype("int64")
    flattened_data = flattened_data.reshape([len(flattened_data), 1])
    res = fluid.LoDTensor()
    res.set(flattened_data, place)
    res.set_lod([lod])
    return res


if __name__ == '__main__':
    main()