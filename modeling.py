# coding=utf-8
# Copyright 2018 The Google AI Language Team Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The main BERT model and related functions."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import copy
import json
import math
import re
import numpy as np
import six
import tensorflow as tf


class BertConfig(object):
  """Configuration for `BertModel`."""

  def __init__(self,
               vocab_size,    # 词表大小
               hidden_size=768,    # 隐藏层神经元数
               num_hidden_layers=12,    # Transformer encoder中的隐藏层数
               num_attention_heads=12,    # multi-head attention 的head数
               intermediate_size=3072,    # encoder的“中间”隐层神经元数（例如feed-forward layer）
               hidden_act="gelu",    # 隐藏层激活函数
               hidden_dropout_prob=0.1,    # 隐藏层dropoit率
               attention_probs_dropout_prob=0.1,    # 注意力部分的dropout
               max_position_embeddings=512,    # 最大位置编码
               type_vocab_size=16,    # token_type_ids的词典大小，其实就是在next_sentence_prediction任务里的Segment A和Segment B。在下载的bert_config.json文件里也有说明，默认值为2
               initializer_range=0.02):    # truncated_normal_initializer初始化方法的stdev
    """Constructs BertConfig.

    Args:
      vocab_size: Vocabulary size of `inputs_ids` in `BertModel`.
      hidden_size: Size of the encoder layers and the pooler layer.
      num_hidden_layers: Number of hidden layers in the Transformer encoder.
      num_attention_heads: Number of attention heads for each attention layer in
        the Transformer encoder.
      intermediate_size: The size of the "intermediate" (i.e., feed-forward)
        layer in the Transformer encoder.
      hidden_act: The non-linear activation function (function or string) in the
        encoder and pooler.
      hidden_dropout_prob: The dropout probability for all fully connected
        layers in the embeddings, encoder, and pooler.
      attention_probs_dropout_prob: The dropout ratio for the attention
        probabilities.
      max_position_embeddings: The maximum sequence length that this model might
        ever be used with. Typically set this to something large just in case
        (e.g., 512 or 1024 or 2048).
      type_vocab_size: The vocabulary size of the `token_type_ids` passed into
        `BertModel`.
      initializer_range: The stdev of the truncated_normal_initializer for
        initializing all weight matrices.
    """
    self.vocab_size = vocab_size
    self.hidden_size = hidden_size
    self.num_hidden_layers = num_hidden_layers
    self.num_attention_heads = num_attention_heads
    self.hidden_act = hidden_act
    self.intermediate_size = intermediate_size
    self.hidden_dropout_prob = hidden_dropout_prob
    self.attention_probs_dropout_prob = attention_probs_dropout_prob
    self.max_position_embeddings = max_position_embeddings
    self.type_vocab_size = type_vocab_size
    self.initializer_range = initializer_range

  @classmethod
  def from_dict(cls, json_object):
    """Constructs a `BertConfig` from a Python dictionary of parameters."""
    config = BertConfig(vocab_size=None)
    for (key, value) in six.iteritems(json_object):
      config.__dict__[key] = value
    return config

  @classmethod
  def from_json_file(cls, json_file):
    """Constructs a `BertConfig` from a json file of parameters."""
    with tf.gfile.GFile(json_file, "r") as reader:
      text = reader.read()
    return cls.from_dict(json.loads(text))

  def to_dict(self):
    """Serializes this instance to a Python dictionary."""
    output = copy.deepcopy(self.__dict__)
    return output

  def to_json_string(self):
    """Serializes this instance to a JSON string."""
    return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


class BertModel(object):
  """BERT model ("Bidirectional Encoder Representations from Transformers").

  Example usage:

  ```python
  # Already been converted into WordPiece token ids
  input_ids = tf.constant([[31, 51, 99], [15, 5, 0]])
  input_mask = tf.constant([[1, 1, 1], [1, 1, 0]])
  token_type_ids = tf.constant([[0, 0, 1], [0, 2, 0]])

  config = modeling.BertConfig(vocab_size=32000, hidden_size=512,
    num_hidden_layers=8, num_attention_heads=6, intermediate_size=1024)

  model = modeling.BertModel(config=config, is_training=True,
    input_ids=input_ids, input_mask=input_mask, token_type_ids=token_type_ids)

  label_embeddings = tf.get_variable(...)
  pooled_output = model.get_pooled_output()
  logits = tf.matmul(pooled_output, label_embeddings)
  ...
  ```
  """

  def __init__(self,
               config,
               is_training,
               input_ids,
               input_mask=None,
               token_type_ids=None,
               use_one_hot_embeddings=False,
               scope=None):
    """Constructor for BertModel.

    Args:
      config: `BertConfig` instance. 对象
      is_training: bool. true for training model, false for eval model. Controls 
        whether dropout will be applied. 表示训练还是eval，是会影响dropout
      input_ids: int32 Tensor of shape [batch_size, seq_length].
      input_mask: (optional) int32 Tensor of shape [batch_size, seq_length].
      token_type_ids: (optional) int32 Tensor of shape [batch_size, seq_length].
      use_one_hot_embeddings: (optional) bool. Whether to use one-hot word 
        embeddings or tf.embedding_lookup() for the word embeddings. 如果True，使用矩阵乘法实现提取词的Embedding；否则用tf.embedding_lookup()，对于TPU，使用前者更快，对于GPU和CPU，后者更快
      scope: (optional) variable scope. Defaults to "bert". 变量的scope。默认是"bert"

    Raises:
      ValueError: The config is invalid or one of the input tensor shapes
        is invalid. 如果config或者输入tensor的shape有问题就会抛出这个异常
    """
    config = copy.deepcopy(config)    # 对config(BertConfig对象)深度拷贝一份
    if not is_training:    # 如果不是训练，那么把dropout都置为零
      config.hidden_dropout_prob = 0.0
      config.attention_probs_dropout_prob = 0.0

    input_shape = get_shape_list(input_ids, expected_rank=2)
    batch_size = input_shape[0]
    seq_length = input_shape[1]

    # 如果输入的input_mask为None，那么构造一个shape合适值全为1的input_mask，这表示输入都是”真实”的输入，没有padding的内容。
    if input_mask is None:
      input_mask = tf.ones(shape=[batch_size, seq_length], dtype=tf.int32)
    # 如果token_type_ids为None，那么构造一个shape合适并且值全为0的tensor，表示所有Token都属于第一个句子。
    if token_type_ids is None:
      token_type_ids = tf.zeros(shape=[batch_size, seq_length], dtype=tf.int32)

    with tf.variable_scope(scope, default_name="bert"):
      with tf.variable_scope("embeddings"):
        # Perform embedding lookup on the word ids. # 词的Embedding lookup
        (self.embedding_output, self.embedding_table) = embedding_lookup(
            input_ids=input_ids,
            vocab_size=config.vocab_size,
            embedding_size=config.hidden_size,
            initializer_range=config.initializer_range,
            word_embedding_name="word_embeddings",
            use_one_hot_embeddings=use_one_hot_embeddings)

        # Add positional embeddings and token type embeddings, then layer # 增加位置embeddings和token type的embeddings，然后是 layer normalize和dropout。
        # normalize and perform dropout. 
        self.embedding_output = embedding_postprocessor(
            input_tensor=self.embedding_output,
            use_token_type=True,
            token_type_ids=token_type_ids,
            token_type_vocab_size=config.type_vocab_size,
            token_type_embedding_name="token_type_embeddings",
            use_position_embeddings=True,
            position_embedding_name="position_embeddings",
            initializer_range=config.initializer_range,
            max_position_embeddings=config.max_position_embeddings,
            dropout_prob=config.hidden_dropout_prob)

      with tf.variable_scope("encoder"):
        # This converts a 2D mask of shape [batch_size, seq_length] to a 3D
        # mask of shape [batch_size, seq_length, seq_length] which is used
        # for the attention scores.
        # 把shape为[batch_size, seq_length]的2D mask变成
		    # shape为[batch_size, seq_length, seq_length]的3D mask
		    # 以便后向的attention计算，例如# input_ids是经过padding的word_ids：[25, 120, 34, 0, 0]，input_mask是有效词标记：[1, 1, 1, 0, 0]
        attention_mask = create_attention_mask_from_input_mask(
            input_ids, input_mask)

        # Run the stacked transformer.
        # `sequence_output` shape = [batch_size, seq_length, hidden_size].
        # 多个Transformer模型stack起来。
  		  # all_encoder_layers是一个list，长度为num_hidden_layers（默认12），每一层对应一个值。
	  	  # 每一个值都是一个shape为[batch_size, seq_length, hidden_size]的tensor。
        self.all_encoder_layers = transformer_model(
            input_tensor=self.embedding_output,
            attention_mask=attention_mask,
            hidden_size=config.hidden_size,
            num_hidden_layers=config.num_hidden_layers,
            num_attention_heads=config.num_attention_heads,
            intermediate_size=config.intermediate_size,
            intermediate_act_fn=get_activation(config.hidden_act),
            hidden_dropout_prob=config.hidden_dropout_prob,
            attention_probs_dropout_prob=config.attention_probs_dropout_prob,
            initializer_range=config.initializer_range,
            do_return_all_layers=True)

      # `sequence_output` 是最后一层的输出，shape是[batch_size, seq_length, hidden_size]
      self.sequence_output = self.all_encoder_layers[-1]
      # The "pooler" converts the encoded sequence tensor of shape
      # [batch_size, seq_length, hidden_size] to a tensor of shape
      # [batch_size, hidden_size]. This is necessary for segment-level
      # (or segment-pair-level) classification tasks where we need a fixed
      # dimensional representation of the segment.
      with tf.variable_scope("pooler"):
        # We "pool" the model by simply taking the hidden state corresponding
        # to the first token. We assume that this has been pre-trained
        # 取最后一层的第一个时刻[CLS]对应的tensor
		    # 从[batch_size, seq_length, hidden_size]变成[batch_size, hidden_size]
		    # sequence_output[:, 0:1, :]得到的是[batch_size, 1, hidden_size]
		    # 我们需要用squeeze把第二维去掉。
        first_token_tensor = tf.squeeze(self.sequence_output[:, 0:1, :], axis=1)
        # 然后再加一个全连接层，输出仍然是[batch_size, hidden_size]
        self.pooled_output = tf.layers.dense(
            first_token_tensor,
            config.hidden_size,
            activation=tf.tanh,
            kernel_initializer=create_initializer(config.initializer_range))

  def get_pooled_output(self):
    return self.pooled_output

  def get_sequence_output(self):
    """Gets final hidden layer of encoder.

    Returns:
      float Tensor of shape [batch_size, seq_length, hidden_size] corresponding
      to the final hidden of the transformer encoder.
    """
    return self.sequence_output

  def get_all_encoder_layers(self):
    return self.all_encoder_layers

  def get_embedding_output(self):
    """Gets output of the embedding lookup (i.e., input to the transformer).

    Returns:
      float Tensor of shape [batch_size, seq_length, hidden_size] corresponding
      to the output of the embedding layer, after summing the word
      embeddings with the positional embeddings and the token type embeddings,
      then performing layer normalization. This is the input to the transformer.
    """
    return self.embedding_output

  def get_embedding_table(self):
    return self.embedding_table


def gelu(x):
  """Gaussian Error Linear Unit.

  This is a smoother version of the RELU.
  Original paper: https://arxiv.org/abs/1606.08415
  Args:
    x: float Tensor to perform activation.

  Returns:
    `x` with the GELU activation applied.
  """
  cdf = 0.5 * (1.0 + tf.tanh(
      (np.sqrt(2 / np.pi) * (x + 0.044715 * tf.pow(x, 3)))))
  return x * cdf


def get_activation(activation_string):
  """Maps a string to a Python function, e.g., "relu" => `tf.nn.relu`.

  Args:
    activation_string: String name of the activation function.

  Returns:
    A Python function corresponding to the activation function. If
    `activation_string` is None, empty, or "linear", this will return None.
    If `activation_string` is not a string, it will return `activation_string`.

  Raises:
    ValueError: The `activation_string` does not correspond to a known
      activation.
  """

  # We assume that anything that"s not a string is already an activation
  # function, so we just return it.
  if not isinstance(activation_string, six.string_types):
    return activation_string

  if not activation_string:
    return None

  act = activation_string.lower()
  if act == "linear":
    return None
  elif act == "relu":
    return tf.nn.relu
  elif act == "gelu":
    return gelu
  elif act == "tanh":
    return tf.tanh
  else:
    raise ValueError("Unsupported activation: %s" % act)


def get_assignment_map_from_checkpoint(tvars, init_checkpoint):
  """Compute the union of the current variables and checkpoint variables."""
  assignment_map = {}
  initialized_variable_names = {}

  name_to_variable = collections.OrderedDict()
  for var in tvars:
    name = var.name
    m = re.match("^(.*):\\d+$", name)
    if m is not None:
      name = m.group(1)
    name_to_variable[name] = var

  init_vars = tf.train.list_variables(init_checkpoint)

  assignment_map = collections.OrderedDict()
  for x in init_vars:
    (name, var) = (x[0], x[1])
    if name not in name_to_variable:
      continue
    assignment_map[name] = name
    initialized_variable_names[name] = 1
    initialized_variable_names[name + ":0"] = 1

  return (assignment_map, initialized_variable_names)


def dropout(input_tensor, dropout_prob):
  """Perform dropout.

  Args:
    input_tensor: float Tensor.
    dropout_prob: Python float. The probability of dropping out a value (NOT of
      *keeping* a dimension as in `tf.nn.dropout`).

  Returns:
    A version of `input_tensor` with dropout applied.
  """
  if dropout_prob is None or dropout_prob == 0.0:
    return input_tensor

  output = tf.nn.dropout(input_tensor, 1.0 - dropout_prob)
  return output


def layer_norm(input_tensor, name=None):
  """Run layer normalization on the last dimension of the tensor."""
  return tf.contrib.layers.layer_norm(
      inputs=input_tensor, begin_norm_axis=-1, begin_params_axis=-1, scope=name)


def layer_norm_and_dropout(input_tensor, dropout_prob, name=None):
  """Runs layer normalization followed by dropout."""
  output_tensor = layer_norm(input_tensor, name)
  output_tensor = dropout(output_tensor, dropout_prob)
  return output_tensor


def create_initializer(initializer_range=0.02):
  """Creates a `truncated_normal_initializer` with the given range."""
  return tf.truncated_normal_initializer(stddev=initializer_range)


def embedding_lookup(input_ids,
                     vocab_size,
                     embedding_size=128,
                     initializer_range=0.02,
                     word_embedding_name="word_embeddings",
                     use_one_hot_embeddings=False):
  """Looks up words embeddings for id tensor.

  Args:
    input_ids: int32 Tensor of shape [batch_size, seq_length] containing word
      ids. 表示WordPiece的id
    vocab_size: int. Size of the embedding vocabulary. 词典大小，需要于vocab.txt一致
    embedding_size: int. Width of the word embeddings. embedding后向量的大小
    initializer_range: float. Embedding initialization range. 随机初始化的范围
    word_embedding_name: string. Name of the embedding table. 名字，默认是"word_embeddings"
    use_one_hot_embeddings: bool. If True, use one-hot method for word 如果True，使用one-hot方法实现embedding；
      embeddings. If False, use `tf.gather()`. 否则，TPU适合用One hot方法。

  Returns:
    float Tensor of shape [batch_size, seq_length, embedding_size].
  """
  # This function assumes that the input is of shape [batch_size, seq_length,
  # num_inputs].
  #
  # If the input is a 2D tensor of shape [batch_size, seq_length], we
  # reshape to [batch_size, seq_length, 1].
  # 这个函数假设输入的shape是[batch_size, seq_length, num_inputs]
	# 普通的Embeding一般假设输入是[batch_size, seq_length]，
	# 增加num_inputs这一维度的目的是为了一次计算更多的Embedding
	# 但目前的代码并没有用到，传入的input_ids都是2D的，这增加了代码的阅读难度。
	
	# 如果输入是[batch_size, seq_length]，
	# 那么我们把它 reshape成[batch_size, seq_length, 1]
  if input_ids.shape.ndims == 2:
    input_ids = tf.expand_dims(input_ids, axis=[-1])

  # 构造Embedding矩阵，shape是[vocab_size, embedding_size]
  embedding_table = tf.get_variable(
      name=word_embedding_name,
      shape=[vocab_size, embedding_size],
      initializer=create_initializer(initializer_range))

  flat_input_ids = tf.reshape(input_ids, [-1])
  if use_one_hot_embeddings:
    one_hot_input_ids = tf.one_hot(flat_input_ids, depth=vocab_size)
    output = tf.matmul(one_hot_input_ids, embedding_table)
  else:
    output = tf.gather(embedding_table, flat_input_ids)

  input_shape = get_shape_list(input_ids)
  # 把输出从[batch_size, seq_length, num_inputs(这里总是1), embedding_size]
	# 变成[batch_size, seq_length, num_inputs*embedding_size]
  output = tf.reshape(output,
                      input_shape[0:-1] + [input_shape[-1] * embedding_size])
  return (output, embedding_table)


def embedding_postprocessor(input_tensor,
                            use_token_type=False,
                            token_type_ids=None,
                            token_type_vocab_size=16,
                            token_type_embedding_name="token_type_embeddings",
                            use_position_embeddings=True,
                            position_embedding_name="position_embeddings",
                            initializer_range=0.02,
                            max_position_embeddings=512,
                            dropout_prob=0.1):
  """Performs various post-processing on a word embedding tensor. 对word embedding之后的tensor进行后处理

  Args:
    input_tensor: float Tensor of shape [batch_size, seq_length,
      embedding_size].
    use_token_type: bool. Whether to add embeddings for `token_type_ids`. 是否增加`token_type_ids`的Embedding
    token_type_ids: (optional) int32 Tensor of shape [batch_size, seq_length].
      Must be specified if `use_token_type` is True. 如果`use_token_type`为True则必须有值
    token_type_vocab_size: int. The vocabulary size of `token_type_ids`. Token Type的个数，通常是2
    token_type_embedding_name: string. The name of the embedding table variable 
      for token type ids. oken type Embedding的名字
    use_position_embeddings: bool. Whether to add position embeddings for the
      position of each token in the sequence. 是否使用位置Embedding
    position_embedding_name: string. The name of the embedding table variable
      for positional embeddings. 位置embedding的名字
    initializer_range: float. Range of the weight initialization. 初始化范围
    max_position_embeddings: int. Maximum sequence length that might ever be
      used with this model. This can be longer than the sequence length of
      input_tensor, but cannot be shorter. 位置编码的最大长度，可以比最大序列长度大，但是不能比它小。
    dropout_prob: float. Dropout probability applied to the final output tensor. Dropout 概率

  Returns:
    float tensor with same shape as `input_tensor`.

  Raises:
    ValueError: One of the tensor shapes or input values is invalid.
  """
  input_shape = get_shape_list(input_tensor, expected_rank=3)
  batch_size = input_shape[0]
  seq_length = input_shape[1]
  width = input_shape[2]

  output = input_tensor

  # Segmentposition信息
  if use_token_type:
    if token_type_ids is None:
      raise ValueError("`token_type_ids` must be specified if"
                       "`use_token_type` is True.")
    token_type_table = tf.get_variable(
        name=token_type_embedding_name,
        shape=[token_type_vocab_size, width],
        initializer=create_initializer(initializer_range))
    # This vocab will be small so we always do one-hot here, since it is always
    # faster for a small vocabulary.
    # 因为Token Type通常很小(2)，所以直接用矩阵乘法(one-hot)更快
    flat_token_type_ids = tf.reshape(token_type_ids, [-1])
    one_hot_ids = tf.one_hot(flat_token_type_ids, depth=token_type_vocab_size)
    token_type_embeddings = tf.matmul(one_hot_ids, token_type_table)
    token_type_embeddings = tf.reshape(token_type_embeddings,
                                       [batch_size, seq_length, width])
    output += token_type_embeddings

  if use_position_embeddings:
    assert_op = tf.assert_less_equal(seq_length, max_position_embeddings)
    with tf.control_dependencies([assert_op]):
      full_position_embeddings = tf.get_variable(
          name=position_embedding_name,
          shape=[max_position_embeddings, width],
          initializer=create_initializer(initializer_range))
      # Since the position embedding table is a learned variable, we create it
      # using a (long) sequence length `max_position_embeddings`. The actual
      # sequence length might be shorter than this, for faster training of
      # tasks that do not have long sequences.
      #
      # So `full_position_embeddings` is effectively an embedding table
      # for position [0, 1, 2, ..., max_position_embeddings-1], and the current
      # sequence has positions [0, 1, 2, ... seq_length-1], so we can just
      # perform a slice.
      # 位置Embedding是可以学习的参数，因此我们创建一个[max_position_embeddings, width]的矩阵
	  	# 但实际输入的序列可能并不会到max_position_embeddings(512)，为了提高训练速度，
	  	# 我们通过tf.slice取出[0, 1, 2, ..., seq_length-1]的部分,。
      position_embeddings = tf.slice(full_position_embeddings, [0, 0],
                                     [seq_length, -1])
      num_dims = len(output.shape.as_list())

      # Only the last two dimensions are relevant (`seq_length` and `width`), so
      # we broadcast among the first dimensions, which is typically just
      # the batch size.
      # word embedding之后的tensor是[batch_size, seq_length, width]
	  	# 因为位置编码是与输入内容无关，它的shape总是[seq_length, width]
		  # 我们无法把位置Embedding加到word embedding上
	  	# 因此我们需要扩展位置编码为[1, seq_length, width]
		  # 然后就能通过broadcasting加上去了。
      position_broadcast_shape = []
      for _ in range(num_dims - 2):
        position_broadcast_shape.append(1)
      position_broadcast_shape.extend([seq_length, width])
      # 默认情况下position_broadcast_shape为[1, 128, 768]
      position_embeddings = tf.reshape(position_embeddings,
                                       position_broadcast_shape)
      # output是[8, 128, 768], position_embeddings是[1, 128, 768]
		  # 因此可以通过broadcasting相加。
      output += position_embeddings

  output = layer_norm_and_dropout(output, dropout_prob)
  return output


def create_attention_mask_from_input_mask(from_tensor, to_mask):
  """Create 3D attention mask from a 2D tensor mask.

  Args:
    from_tensor: 2D or 3D Tensor of shape [batch_size, from_seq_length, ...].
    to_mask: int32 Tensor of shape [batch_size, to_seq_length].

  Returns:
    float Tensor of shape [batch_size, from_seq_length, to_seq_length].
  """
  # 确保输入的tensor是2D或者3D，前两维是batch_size和from_seq_length
  from_shape = get_shape_list(from_tensor, expected_rank=[2, 3])
  batch_size = from_shape[0]
  from_seq_length = from_shape[1]

  # 确保to_mask是2D的，shape是[batch_size, to_seq_length]
  to_shape = get_shape_list(to_mask, expected_rank=2)
  to_seq_length = to_shape[1]

  # 1. 把to_mask的shape从[batch_size, to_seq_length]转成[batch_size, 1, to_seq_length]
  # 2. 把to_mask的数据类型从int32转成float
  to_mask = tf.cast(
      tf.reshape(to_mask, [batch_size, 1, to_seq_length]), tf.float32)

  # We don't assume that `from_tensor` is a mask (although it could be). We
  # don't actually care if we attend *from* padding tokens (only *to* padding)
  # tokens so we create a tensor of all ones.
  #
  # `broadcast_ones` = [batch_size, from_seq_length, 1]
  # broadcast_ones是一个float32的[batch_size, from_seq_length, 1]的全1 tensor
  broadcast_ones = tf.ones(
      shape=[batch_size, from_seq_length, 1], dtype=tf.float32)

  # Here we broadcast along two dimensions to create the mask.
  # [batch_size, from_seq_length, 1]和[[batch_size, 1, from_seq_length]相乘(element-wise乘积)，经过broadcast后
  # 得到[batch_size, from_seq_length, from_seq_length]
  mask = broadcast_ones * to_mask

  return mask


def attention_layer(from_tensor,
                    to_tensor,
                    attention_mask=None,
                    num_attention_heads=1,
                    size_per_head=512,
                    query_act=None,
                    key_act=None,
                    value_act=None,
                    attention_probs_dropout_prob=0.0,
                    initializer_range=0.02,
                    do_return_2d_tensor=False,
                    batch_size=None,
                    from_seq_length=None,
                    to_seq_length=None):
  """Performs multi-headed attention from `from_tensor` to `to_tensor`. 用`from_tensor`(作为Query)去attend to `to_tensor`(提供Key和Value)

  This is an implementation of multi-headed attention based on "Attention
  is all you Need". If `from_tensor` and `to_tensor` are the same, then
  this is self-attention. Each timestep in `from_tensor` attends to the
  corresponding sequence in `to_tensor`, and returns a fixed-with vector.

  This function first projects `from_tensor` into a "query" tensor and
  `to_tensor` into "key" and "value" tensors. These are (effectively) a list
  of tensors of length `num_attention_heads`, where each tensor is of shape
  [batch_size, seq_length, size_per_head].

  Then, the query and key tensors are dot-producted and scaled. These are
  softmaxed to obtain attention probabilities. The value tensors are then
  interpolated by these probabilities, then concatenated back to a single
  tensor and returned.

  In practice, the multi-headed attention are done with transposes and
  reshapes rather than actual separate tensors.

  这个函数实现论文"Attention
	is all you Need"里的multi-head attention。
	如果`from_tensor`和`to_tensor`是同一个tensor，那么就实现Self-Attention。
	`from_tensor`的每个时刻都会attends to `to_tensor`，
        也就是用from的Query去乘以所有to的Key，得到weight，然后把所有to的Value加权求和起来。
	
	这个函数首先把`from_tensor`变换成一个"query" tensor，
        然后把`to_tensor`变成"key"和"value" tensors。
        总共有`num_attention_heads`组Query、Key和Value，
        每一个Query，Key和Value的shape都是[batch_size(8), seq_length(128), size_per_head(512/8=64)].
	
	然后计算query和key的内积并且除以size_per_head的平方根(8)。
        然后softmax变成概率，最后用概率加权value得到输出。
        因为有多个Head，每个Head都输出[batch_size, seq_length, size_per_head]，
        最后把8个Head的结果concat起来，就最终得到[batch_size(8), seq_length(128), size_per_head*8=512] 
	
	实际上我们是把这8个Head的Query，Key和Value都放在一个Tensor里面的，
        因此实际通过transpose和reshape就达到了上面的效果

  Args:
    from_tensor: float Tensor of shape [batch_size, from_seq_length,
      from_width].
    to_tensor: float Tensor of shape [batch_size, to_seq_length, to_width].
    attention_mask: (optional) int32 Tensor of shape [batch_size,
      from_seq_length, to_seq_length]. The values should be 1 or 0. The
      attention scores will effectively be set to -infinity for any positions in
      the mask that are 0, and will be unchanged for positions that are 1. 
      值可以是0或者1，在计算attention score的时候，
      我们会把0变成负无穷(实际是一个绝对值很大的负数)，而1不变，
      这样softmax的时候进行exp的计算，前者就趋近于零，从而间接实现Mask的功能。
    num_attention_heads: int. Number of attention heads. Attention heads的数量
    size_per_head: int. Size of each attention head. 每个head的size
    query_act: (optional) Activation function for the query transform. query变换的激活函数
    key_act: (optional) Activation function for the key transform. key变换的激活函数
    value_act: (optional) Activation function for the value transform. value变换的激活函数
    attention_probs_dropout_prob: (optional) float. Dropout probability of the
      attention probabilities. attention的Dropout概率
    initializer_range: float. Range of the weight initializer. 初始化范围
    do_return_2d_tensor: bool. If True, the output will be of shape [batch_size
      * from_seq_length, num_attention_heads * size_per_head]. If False, the
      output will be of shape [batch_size, from_seq_length, num_attention_heads
      * size_per_head].
      如果True，返回2D的Tensor其shape是[batch_size * from_seq_length, num_attention_heads * size_per_head]；
      否则返回3D的Tensor其shape为[batch_size, from_seq_length, num_attention_heads * size_per_head].
    batch_size: (Optional) int. If the input is 2D, this might be the batch size
      of the 3D version of the `from_tensor` and `to_tensor`.如果输入是3D的，那么batch就是第一维，但是可能3D的压缩成了2D的，所以需要告诉函数batch_size 
    from_seq_length: (Optional) If the input is 2D, this might be the seq length
      of the 3D version of the `from_tensor`. 同上，需要告诉函数from_seq_length
    to_seq_length: (Optional) If the input is 2D, this might be the seq length
      of the 3D version of the `to_tensor`.

  Returns:
    float Tensor of shape [batch_size, from_seq_length,
      num_attention_heads * size_per_head]. (If `do_return_2d_tensor` is
      true, this will be of shape [batch_size * from_seq_length,
      num_attention_heads * size_per_head]).
      如果`do_return_2d_tensor`为True，则返回的shape是[batch_size * from_seq_length, num_attention_heads * size_per_head].

  Raises:
    ValueError: Any of the arguments or tensor shapes are invalid.
  """

  def transpose_for_scores(input_tensor, batch_size, num_attention_heads,
                           seq_length, width):
    output_tensor = tf.reshape(
        input_tensor, [batch_size, seq_length, num_attention_heads, width])

    output_tensor = tf.transpose(output_tensor, [0, 2, 1, 3])
    return output_tensor

  from_shape = get_shape_list(from_tensor, expected_rank=[2, 3])
  to_shape = get_shape_list(to_tensor, expected_rank=[2, 3])

  if len(from_shape) != len(to_shape):
    raise ValueError(
        "The rank of `from_tensor` must match the rank of `to_tensor`.")

  # 如果输入是3D的(没有压缩)，那么我们可以推测出batch_size、from_seq_length和to_seq_length
	# 即使参数传入也会被覆盖。
  if len(from_shape) == 3:
    batch_size = from_shape[0]
    from_seq_length = from_shape[1]
    to_seq_length = to_shape[1]
  elif len(from_shape) == 2:  # 如果是压缩成2D的，那么一定要传入这3个参数，否则抛异常。
    if (batch_size is None or from_seq_length is None or to_seq_length is None):
      raise ValueError(
          "When passing in rank 2 tensors to attention_layer, the values "
          "for `batch_size`, `from_seq_length`, and `to_seq_length` "
          "must all be specified.")

  # Scalar dimensions referenced here:
  #   B = batch size (number of sequences) 默认配置是8
  #   F = `from_tensor` sequence length 默认配置是128
  #   T = `to_tensor` sequence length 默认配置是128
  #   N = `num_attention_heads` 默认配置是12
  #   H = `size_per_head` 默认配置是64
  
  # 把from和to压缩成2D的。
	# [8*128, 768]
  from_tensor_2d = reshape_to_matrix(from_tensor)
  # [8*128, 768]
  to_tensor_2d = reshape_to_matrix(to_tensor)

  # `query_layer` = [B*F, N*H]
  # 计算Query `query_layer` = [B*F, N*H] =[8*128, 12*64]
	# batch_size=8，共128个时刻，12和head，每个head的query向量是64
	# 因此最终得到[8*128, 12*64]
  query_layer = tf.layers.dense(
      from_tensor_2d,
      num_attention_heads * size_per_head,
      activation=query_act,
      name="query",
      kernel_initializer=create_initializer(initializer_range))

  # `key_layer` = [B*T, N*H]
  # 和query类似，`key_layer` = [B*T, N*H]
  key_layer = tf.layers.dense(
      to_tensor_2d,
      num_attention_heads * size_per_head,
      activation=key_act,
      name="key",
      kernel_initializer=create_initializer(initializer_range))

  # `value_layer` = [B*T, N*H]
  # 同上，`value_layer` = [B*T, N*H]
  value_layer = tf.layers.dense(
      to_tensor_2d,
      num_attention_heads * size_per_head,
      activation=value_act,
      name="value",
      kernel_initializer=create_initializer(initializer_range))

  # `query_layer` = [B, N, F, H]
  # 把query从[B*F, N*H] =[8*128, 12*64]变成[B, N, F, H]=[8, 12, 128, 64]
  query_layer = transpose_for_scores(query_layer, batch_size,
                                     num_attention_heads, from_seq_length,
                                     size_per_head)

  # `key_layer` = [B, N, T, H]
  # 同上，key也变成[8, 12, 128, 64]
  key_layer = transpose_for_scores(key_layer, batch_size, num_attention_heads,
                                   to_seq_length, size_per_head)

  # Take the dot product between "query" and "key" to get the raw
  # attention scores.
  # `attention_scores` = [B, N, F, T]
  # 计算query和key的内积，得到attention scores.
	# [8, 12, 128, 64]*[8, 12, 64, 128]=[8, 12, 128, 128]
	# 最后两维[128, 128]表示from的128个时刻attend to到to的128个score。
	# `attention_scores` = [B, N, F, T]
  attention_scores = tf.matmul(query_layer, key_layer, transpose_b=True)
  attention_scores = tf.multiply(attention_scores,
                                 1.0 / math.sqrt(float(size_per_head)))

  if attention_mask is not None:
    # 从[8, 128, 128]变成[8, 1, 128, 128]
    # `attention_mask` = [B, 1, F, T]
    attention_mask = tf.expand_dims(attention_mask, axis=[1])

    # Since attention_mask is 1.0 for positions we want to attend and 0.0 for
    # masked positions, this operation will create a tensor which is 0.0 for
    # positions we want to attend and -10000.0 for masked positions.
    # 这个小技巧前面也用到过，如果mask是1，那么(1-1)*-10000=0，adder就是0,
		# 如果mask是0，那么(1-0)*-10000=-10000。
    adder = (1.0 - tf.cast(attention_mask, tf.float32)) * -10000.0

    # Since we are adding it to the raw scores before the softmax, this is
    # effectively the same as removing these entirely.
    # 我们把adder加到attention_score里，mask是1就相当于加0，mask是0就相当于加-10000。
		# 通常attention_score都不会很大，因此mask为0就相当于把attention_score设置为负无穷
		# 后面softmax的时候就趋近于0，因此相当于不能attend to Mask为0的地方。
    attention_scores += adder

  # softmax
  # Normalize the attention scores to probabilities.
  # `attention_probs` = [B, N, F, T]
  attention_probs = tf.nn.softmax(attention_scores)

  # This is actually dropping out entire tokens to attend to, which might
  # seem a bit unusual, but is taken from the original Transformer paper.
  # 对attention_probs进行dropout，这虽然有点奇怪，但是Transformer的原始论文就是这么干的。
  attention_probs = dropout(attention_probs, attention_probs_dropout_prob)

  # 把`value_layer` reshape成[B, T, N, H]=[8, 128, 12, 64]
  # `value_layer` = [B, T, N, H]
  value_layer = tf.reshape(
      value_layer,
      [batch_size, to_seq_length, num_attention_heads, size_per_head])

  # `value_layer`变成[B, N, T, H]=[8, 12, 128, 64]
  # `value_layer` = [B, N, T, H]
  value_layer = tf.transpose(value_layer, [0, 2, 1, 3])

  # 计算`context_layer` = [8, 12, 128, 128]*[8, 12, 128, 64]=[8, 12, 128, 64]=[B, N, F, H]
  # `context_layer` = [B, N, F, H]
  context_layer = tf.matmul(attention_probs, value_layer)

  # `context_layer` 变换成 [B, F, N, H]=[8, 128, 12, 64]
  # `context_layer` = [B, F, N, H]
  context_layer = tf.transpose(context_layer, [0, 2, 1, 3])

  if do_return_2d_tensor:
    # `context_layer` = [B*F, N*H]
    context_layer = tf.reshape(
        context_layer,
        [batch_size * from_seq_length, num_attention_heads * size_per_head])
  else:
    # `context_layer` = [B, F, N*H]
    context_layer = tf.reshape(
        context_layer,
        [batch_size, from_seq_length, num_attention_heads * size_per_head])

  return context_layer


def transformer_model(input_tensor,
                      attention_mask=None,
                      hidden_size=768,
                      num_hidden_layers=12,
                      num_attention_heads=12,
                      intermediate_size=3072,
                      intermediate_act_fn=gelu,
                      hidden_dropout_prob=0.1,
                      attention_probs_dropout_prob=0.1,
                      initializer_range=0.02,
                      do_return_all_layers=False):
  """Multi-headed, multi-layer Transformer from "Attention is All You Need".

  This is almost an exact implementation of the original Transformer encoder.
  这基本上是和原始Transformer encoder相同的代码。
  See the original paper:
  https://arxiv.org/abs/1706.03762

  Also see:
  https://github.com/tensorflow/tensor2tensor/blob/master/tensor2tensor/models/transformer.py

  Args:
    input_tensor: float Tensor of shape [batch_size, seq_length, hidden_size].
    attention_mask: (optional) int32 Tensor of shape [batch_size, seq_length,
      seq_length], with 1 for positions that can be attended to and 0 in
      positions that should not be. 1表示可以attend to，0表示不能
    hidden_size: int. Hidden size of the Transformer. ransformer隐单元个数
    num_hidden_layers: int. Number of layers (blocks) in the Transformer. 有多少个SubLayer 
    num_attention_heads: int. Number of attention heads in the Transformer.  Transformer Attention Head个数
    intermediate_size: int. The size of the "intermediate" (a.k.a., feed
      forward) layer. 全连接层的隐单元个数
    intermediate_act_fn: function. The non-linear activation function to apply
      to the output of the intermediate/feed-forward layer. 全连接层的激活函数。
    hidden_dropout_prob: float. Dropout probability for the hidden layers. Self-Attention层残差之前的Dropout概率
    attention_probs_dropout_prob: float. Dropout probability of the attention
      probabilities. attention的Dropout概率
    initializer_range: float. Range of the initializer (stddev of truncated
      normal). 初始化范围(truncated normal的标准差)
    do_return_all_layers: Whether to also return all layers or just the final
      layer. 返回所有层的输出还是最后一层的输出。

  Returns:
    float Tensor of shape [batch_size, seq_length, hidden_size], the final
    hidden layer of the Transformer.
    如果do_return_all_layers True，返回最后一层的输出，是一个Tensor，
                shape为[batch_size, seq_length, hidden_size]；
    否则返回所有层的输出，是一个长度为num_hidden_layers的list，
                list的每一个元素都是[batch_size, seq_length, hidden_size]。

  Raises:
    ValueError: A Tensor shape or parameter is invalid.
  """
  if hidden_size % num_attention_heads != 0:
    raise ValueError(
        "The hidden size (%d) is not a multiple of the number of attention "
        "heads (%d)" % (hidden_size, num_attention_heads))

  # 因为最终要输出hidden_size，总共有num_attention_heads个Head，因此每个Head输出
  # 为hidden_size / num_attention_heads
  attention_head_size = int(hidden_size / num_attention_heads)
  input_shape = get_shape_list(input_tensor, expected_rank=3)
  batch_size = input_shape[0]
  seq_length = input_shape[1]
  input_width = input_shape[2]

  # The Transformer performs sum residuals on all layers so the input needs
  # to be the same as the hidden size.
  # 因为需要残差连接，我们需要把输入加到Self-Attention的输出，因此要求它们的shape是相同的。
  if input_width != hidden_size:
    raise ValueError("The width of the input tensor (%d) != hidden size (%d)" %
                     (input_width, hidden_size))

  # We keep the representation as a 2D tensor to avoid re-shaping it back and
  # forth from a 3D tensor to a 2D tensor. Re-shapes are normally free on
  # the GPU/CPU but may not be free on the TPU, so we want to minimize them to
  # help the optimizer.
  # 为了避免在2D和3D之间来回reshape，我们统一把所有的3D Tensor用2D来表示。
  # 虽然reshape在GPU/CPU上很快，但是在TPU上却不是这样，这样做的目的是为了优化TPU
  # input_tensor是[8, 128, 768], prev_output是[8*128, 768]=[1024, 768]
  prev_output = reshape_to_matrix(input_tensor)

  all_layer_outputs = []
  for layer_idx in range(num_hidden_layers):
    # 每一层都有自己的variable scope
    with tf.variable_scope("layer_%d" % layer_idx):
      layer_input = prev_output
      # attention层
      with tf.variable_scope("attention"):
        attention_heads = []
        # self attention
        with tf.variable_scope("self"):
          attention_head = attention_layer(
              from_tensor=layer_input,
              to_tensor=layer_input,
              attention_mask=attention_mask,
              num_attention_heads=num_attention_heads,
              size_per_head=attention_head_size,
              attention_probs_dropout_prob=attention_probs_dropout_prob,
              initializer_range=initializer_range,
              do_return_2d_tensor=True,
              batch_size=batch_size,
              from_seq_length=seq_length,
              to_seq_length=seq_length)
          attention_heads.append(attention_head)

        attention_output = None
        if len(attention_heads) == 1:
          attention_output = attention_heads[0]
        else:
          # In the case where we have other sequences, we just concatenate
          # them to the self-attention head before the projection.
          # 如果有多个head，那么需要把多个head的输出concat起来
          attention_output = tf.concat(attention_heads, axis=-1)

        # Run a linear projection of `hidden_size` then add a residual
        # with `layer_input`.
        # 使用线性变换把前面的输出变成`hidden_size`，然后再加上`layer_input`(残差连接)
        with tf.variable_scope("output"):
          attention_output = tf.layers.dense(
              attention_output,
              hidden_size,
              kernel_initializer=create_initializer(initializer_range))
          attention_output = dropout(attention_output, hidden_dropout_prob)
          # 残差连接再加上layer norm。
          attention_output = layer_norm(attention_output + layer_input)

      # 全连接层
      # The activation is only applied to the "intermediate" hidden layer.
      with tf.variable_scope("intermediate"):
        intermediate_output = tf.layers.dense(
            attention_output,
            intermediate_size,
            activation=intermediate_act_fn,
            kernel_initializer=create_initializer(initializer_range))

      # 然后是用一个线性变换把大小变回`hidden_size`，这样才能加残差连接
      # Down-project back to `hidden_size` then add the residual.
      with tf.variable_scope("output"):
        layer_output = tf.layers.dense(
            intermediate_output,
            hidden_size,
            kernel_initializer=create_initializer(initializer_range))
        layer_output = dropout(layer_output, hidden_dropout_prob)
        layer_output = layer_norm(layer_output + attention_output)
        prev_output = layer_output
        all_layer_outputs.append(layer_output)

  if do_return_all_layers:
    final_outputs = []
    for layer_output in all_layer_outputs:
      final_output = reshape_from_matrix(layer_output, input_shape)
      final_outputs.append(final_output)
    return final_outputs
  else:
    final_output = reshape_from_matrix(prev_output, input_shape)
    return final_output


def get_shape_list(tensor, expected_rank=None, name=None):
  """Returns a list of the shape of tensor, preferring static dimensions.

  Args:
    tensor: A tf.Tensor object to find the shape of.
    expected_rank: (optional) int. The expected rank of `tensor`. If this is
      specified and the `tensor` has a different rank, and exception will be
      thrown.
    name: Optional name of the tensor for the error message.

  Returns:
    A list of dimensions of the shape of tensor. All static dimensions will
    be returned as python integers, and dynamic dimensions will be returned
    as tf.Tensor scalars.
  """
  if name is None:
    name = tensor.name

  if expected_rank is not None:
    assert_rank(tensor, expected_rank, name)

  shape = tensor.shape.as_list()

  non_static_indexes = []
  for (index, dim) in enumerate(shape):
    if dim is None:
      non_static_indexes.append(index)

  if not non_static_indexes:
    return shape

  dyn_shape = tf.shape(tensor)
  for index in non_static_indexes:
    shape[index] = dyn_shape[index]
  return shape


def reshape_to_matrix(input_tensor):
  """Reshapes a >= rank 2 tensor to a rank 2 tensor (i.e., a matrix)."""
  ndims = input_tensor.shape.ndims
  if ndims < 2:
    raise ValueError("Input tensor must have at least rank 2. Shape = %s" %
                     (input_tensor.shape))
  if ndims == 2:
    return input_tensor

  width = input_tensor.shape[-1]
  output_tensor = tf.reshape(input_tensor, [-1, width])
  return output_tensor


def reshape_from_matrix(output_tensor, orig_shape_list):
  """Reshapes a rank 2 tensor back to its original rank >= 2 tensor."""
  if len(orig_shape_list) == 2:
    return output_tensor

  output_shape = get_shape_list(output_tensor)

  orig_dims = orig_shape_list[0:-1]
  width = output_shape[-1]

  return tf.reshape(output_tensor, orig_dims + [width])


def assert_rank(tensor, expected_rank, name=None):
  """Raises an exception if the tensor rank is not of the expected rank.

  Args:
    tensor: A tf.Tensor to check the rank of.
    expected_rank: Python integer or list of integers, expected rank.
    name: Optional name of the tensor for the error message.

  Raises:
    ValueError: If the expected shape doesn't match the actual shape.
  """
  if name is None:
    name = tensor.name

  expected_rank_dict = {}
  if isinstance(expected_rank, six.integer_types):
    expected_rank_dict[expected_rank] = True
  else:
    for x in expected_rank:
      expected_rank_dict[x] = True

  actual_rank = tensor.shape.ndims
  if actual_rank not in expected_rank_dict:
    scope_name = tf.get_variable_scope().name
    raise ValueError(
        "For the tensor `%s` in scope `%s`, the actual rank "
        "`%d` (shape = %s) is not equal to the expected rank `%s`" %
        (name, scope_name, actual_rank, str(tensor.shape), str(expected_rank)))
