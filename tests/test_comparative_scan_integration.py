import pandas as pd


def test_attention_rows_appended_to_sentiment_signals():
    """Verify that fetch_comparative_interest output is shaped correctly
    for the sentiment_signals_df format used by save_scan."""
    from src.data.trends_symbols import fetch_comparative_interest

    class FakeClient:
        def build_payload(self, kw_list, timeframe=None, geo=None):
            self._terms = kw_list
        def interest_over_time(self):
            return pd.DataFrame({t: [10.0] * 13 for t in self._terms})

    smap = {"US|Technology": ["XLK"], "US|Energy": ["XLE"]}
    attn = fetch_comparative_interest(
        smap, client=FakeClient(), sleep_s=0.0, region_geos={"US": ["US"]},
    )
    rows = []
    for key, val in attn.items():
        region, _, sector = key.partition("|")
        rows.append({
            "region": region,
            "gics_sector": sector,
            "signal_name": "attention_level",
            "value": val,
        })
    df = pd.DataFrame(rows)
    assert set(df.columns) == {"region", "gics_sector", "signal_name", "value"}
    assert (df["signal_name"] == "attention_level").all()
    assert len(df) == 2
