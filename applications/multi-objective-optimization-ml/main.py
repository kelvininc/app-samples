from datetime import timedelta

import pandas as pd
from kelvin.application import KelvinApp
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.logs import logger
from kelvin.message import ControlChange, Recommendation

from multi_objective_optimization import run_model
from settings import Settings

app = KelvinApp()

# All 17 model streams in the order run_model expects: the 13 controllable inputs first (feature
# columns), then the 4 measured targets. train_random_forest_models splits the window positionally
# with df.iloc[:, :13] / df.iloc[:, 13:], so this order is load-bearing.
INPUTS = [
    "wire_part_vacuum_foil_level_set_point",
    "exhaust_fan_3_burner_temperature_set_point",
    "paper_machine_speed_set_point",
    "primary_screen_reject_flow_rate_set_point",
    "turbo_3_vacuum_control_output_set_point",
    "shoe_press_hydration_tank_level",
    "low_pressure_steam_flow_rate_set_point",
    "air_dryer_temperature_set_point",
    "jw_ratio_volume_flow",
    "3p_load_top_side_set_point",
    "mix_pipe_flow_set_point",
    "top_dryers_steam_pressure_set_point",
    "spray_starch_standby_pump_rate_set_point",
    "paper_substance_weight",
    "paper_brightness_top_side",
    "luminance_value_top_side",
    "luminance_value_bottom_side",
]

# Only the first 13 are controllable; run_model also returns the 4 measured targets, which are
# predictions, not control outputs. Keep in sync with control_changes.outputs in app.yaml.
CONTROLLABLE_SET_POINTS = frozenset(INPUTS[:13])


@app.task
async def optimize() -> None:
    """Roll a per-asset window over the 17 streams and recommend optimized set points.

    Window behaviour is app configuration (see settings.Window). count_size counts raw messages,
    so a window of `rows` dense rows needs about `rows * len(INPUTS)` records; round_to collapses
    same-second readings into one row; slide advances `retrain_every_rows` rows between retrains.
    """
    window = Settings(**app.app_configuration).window
    async for asset, df in app.rolling_window(
        count_size=window.rows * len(INPUTS),
        slide=window.retrain_every_rows * len(INPUTS),
        inputs=INPUTS,
        round_to=timedelta(seconds=window.round_seconds),
    ).stream():
        if df.empty:
            continue
        await recommend(asset, df)


async def recommend(asset: str, df: pd.DataFrame) -> None:
    """Fit the models on the window and publish a recommendation with the new set points."""
    try:
        recommended_set_points = run_model(df)
    except Exception as e:                        # one asset's failure must not stop the stream
        logger.error("Optimization failed", asset=asset, error=str(e), error_type=type(e).__name__)
        return

    if not recommended_set_points:                # not enough data yet
        return

    control_changes = [
        ControlChange(
            resource=KRNAssetDataStream(asset, name),
            payload=value,
            expiration_date=timedelta(hours=1),
        )
        for name, value in recommended_set_points.items()
        if name in CONTROLLABLE_SET_POINTS
    ]
    if not control_changes:
        return

    logger.info("Publishing optimization recommendation", asset=asset, set_points=len(control_changes))
    await app.publish(
        Recommendation(
            resource=KRNAsset(asset),
            type="multi_objective_optimization",
            description="Multi-objective optimization",
            control_changes=control_changes,
        )
    )


if __name__ == "__main__":
    app.run()
