import base64
import pickle
import unittest
from unittest.mock import MagicMock, mock_open, patch

from openpilot.selfdrive.modeld import modeld


class TestModelState(unittest.TestCase):
  def test_recurrent_outputs_share_state_without_temporary_allocations(self):
    for chestnut in (False, True):
      with self.subTest(chestnut=chestnut):
        device = 'AMD' if chestnut else 'QCOM'
        specs = {'next_features': ((1, 16), 'float32', device), 'outputs': ((1, 8), 'float32', device)}
        jits = {
          'input_specs': {'new_img': ((1, 2), 'float32', device), 'features': ((1, 16), 'float32', device)},
          'output_specs': specs,
          'metadata': {'output_shapes': {k: v[0] for k, v in specs.items()},
                       'metadata': {'output_slices': base64.b64encode(pickle.dumps({'plan': slice(0, 8)}))}},
          'run': MagicMock(),
        }
        state = MagicMock(shape=(1, 16), dtype='float32')

        def pack_inputs(model, state=state):
          model.input_queues = {'features': state}

        with (patch.object(modeld, 'load_oob', return_value=jits),
              patch.object(modeld.ModelState, 'pack_inputs', pack_inputs),
              patch.object(modeld, 'get_nv12_info', return_value=(4, 4, 2, 24)),
              patch.object(modeld, 'open', mock_open()),
              patch.object(modeld.pickle, 'load', return_value={'run': MagicMock()}),
              patch.object(modeld, 'lower_and_compile'),
              patch.object(modeld, 'Tensor') as tensor,
              patch.object(modeld, 'input_view') as view,
              patch.object(modeld, 'Parser')):
          model = modeld.ModelState(4, 4, chestnut)

        self.assertEqual(tensor.call_count, 1)
        self.assertEqual(tensor.call_args.args[0].shape, (1, 8))
        self.assertEqual(tensor.call_args.kwargs['device'], device)
        view.assert_called_once_with(state._buffer(), state.shape, state.dtype, 0)
        self.assertIs(model.outputs['next_features'], view.return_value)
        self.assertIs(model.outputs['outputs'], tensor.return_value.realize.return_value)
        self.assertEqual(list(model.outputs), list(specs))
