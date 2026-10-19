"""Selection source: TQ_量能回測實戰.ipynb."""
from ._tquant import TQuantStrategy


class VolumeSurgeStrategy(TQuantStrategy):
    name = 'volume_surge'
    display_name = '量能突破'
    source_notebook = 'TQ_量能回測實戰.ipynb'
    selection_description = '當日成交量大於含當日的 4 日平均成交量 2.5 倍。'
    window = 4
    history_columns = ('close_price', 'volume')

    def signal(self, group):
        return group.volume.iloc[-1] > 2.5 * group.volume.mean()
