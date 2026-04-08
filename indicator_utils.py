import math
from typing import Dict, List, Optional


def _to_float_list(values: List[float]) -> List[float]:
    return [float(value) for value in values]


def wma(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    denominator = size * (size + 1) / 2.0
    if len(seq) < size:
        return result

    for index in range(size - 1, len(seq)):
        window = seq[index - size + 1 : index + 1]
        weighted_sum = 0.0
        for weight, value in enumerate(window, start=1):
            weighted_sum += weight * value
        result[index] = weighted_sum / denominator
    return result


def hma(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    if len(seq) < size:
        return result

    half_length = max(int(size / 2), 1)
    sqrt_length = max(int(math.floor(math.sqrt(size))), 1)
    wma_half = wma(seq, half_length)
    wma_full = wma(seq, size)

    diff: List[float] = []
    diff_indices: List[int] = []
    for index in range(len(seq)):
        half_value = wma_half[index]
        full_value = wma_full[index]
        if half_value is None or full_value is None:
            continue
        diff_indices.append(index)
        diff.append(2.0 * float(half_value) - float(full_value))

    diff_hma = wma(diff, sqrt_length)
    for diff_pos, source_index in enumerate(diff_indices):
        result[source_index] = diff_hma[diff_pos]
    return result


def rolling_stddev(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    if len(seq) < size:
        return result

    for index in range(size - 1, len(seq)):
        window = seq[index - size + 1 : index + 1]
        mean = sum(window) / size
        variance = sum((value - mean) ** 2 for value in window) / size
        result[index] = math.sqrt(variance)
    return result


def sma(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    if len(seq) < size:
        return result

    for index in range(size - 1, len(seq)):
        window = seq[index - size + 1 : index + 1]
        result[index] = sum(window) / float(size)
    return result


def ema(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    if len(seq) < size:
        return result

    multiplier = 2.0 / (size + 1.0)
    seed = sum(seq[:size]) / float(size)
    result[size - 1] = seed
    previous = seed
    for index in range(size, len(seq)):
        previous = ((seq[index] - previous) * multiplier) + previous
        result[index] = previous
    return result


def rma(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    if len(seq) < size:
        return result

    seed = sum(seq[:size]) / float(size)
    result[size - 1] = seed
    previous = seed
    for index in range(size, len(seq)):
        previous = ((previous * (size - 1)) + seq[index]) / float(size)
        result[index] = previous
    return result


def bollinger_bands(
    values: List[float],
    length: int,
    mult: float,
) -> Dict[str, List[Optional[float]]]:
    seq = _to_float_list(values)
    basis = sma(seq, length)
    std = rolling_stddev(seq, length)
    upper: List[Optional[float]] = [None] * len(seq)
    lower: List[Optional[float]] = [None] * len(seq)
    for index in range(len(seq)):
        if basis[index] is None or std[index] is None:
            continue
        dev = float(mult) * float(std[index])
        upper[index] = float(basis[index]) + dev
        lower[index] = float(basis[index]) - dev
    return {
        "basis": basis,
        "upper": upper,
        "lower": lower,
        "stddev": std,
    }


def bollinger_bandwidth(
    values: List[float],
    length: int,
    mult: float,
) -> Dict[str, List[Optional[float]]]:
    bands = bollinger_bands(values=values, length=length, mult=mult)
    basis = bands["basis"]
    upper = bands["upper"]
    lower = bands["lower"]
    bbw: List[Optional[float]] = [None] * len(values)
    for index in range(len(values)):
        if basis[index] is None or upper[index] is None or lower[index] is None:
            continue
        basis_value = float(basis[index])
        if abs(basis_value) <= 1e-12:
            bbw[index] = 0.0
            continue
        bbw[index] = ((float(upper[index]) - float(lower[index])) / basis_value) * 100.0
    return {
        "basis": basis,
        "upper": upper,
        "lower": lower,
        "bbw": bbw,
    }


def rolling_highest(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    if len(seq) < size:
        return result

    for index in range(size - 1, len(seq)):
        window = seq[index - size + 1 : index + 1]
        result[index] = max(window)
    return result


def rolling_lowest(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    if len(seq) < size:
        return result

    for index in range(size - 1, len(seq)):
        window = seq[index - size + 1 : index + 1]
        result[index] = min(window)
    return result


def hlc3(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    return [
        (float(high) + float(low) + float(close)) / 3.0
        for high, low, close in zip(highs, lows, closes)
    ]


def chande_momentum_oscillator(values: List[float], length: int) -> List[Optional[float]]:
    size = max(int(length or 0), 1)
    seq = _to_float_list(values)
    result: List[Optional[float]] = [None] * len(seq)
    if len(seq) < size + 1:
        return result

    changes = [None]
    for index in range(1, len(seq)):
        changes.append(seq[index] - seq[index - 1])

    for index in range(size, len(seq)):
        window = changes[index - size + 1 : index + 1]
        positives = sum(change for change in window if change is not None and change >= 0)
        negatives = sum(-change for change in window if change is not None and change < 0)
        denominator = positives + negatives
        if denominator <= 0:
            result[index] = 0.0
        else:
            result[index] = 100.0 * (positives - negatives) / denominator
    return result


def true_range(
    highs: List[float],
    lows: List[float],
    closes: List[float],
) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(highs)
    if not highs or not lows or not closes:
        return result

    for index in range(len(highs)):
        high_value = float(highs[index])
        low_value = float(lows[index])
        if index == 0:
            result[index] = high_value - low_value
            continue
        prev_close = float(closes[index - 1])
        result[index] = max(
            high_value - low_value,
            abs(high_value - prev_close),
            abs(low_value - prev_close),
        )
    return result


def atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    length: int = 14,
    smoothing: str = "RMA",
) -> List[Optional[float]]:
    tr_values = [float(value or 0.0) for value in true_range(highs, lows, closes)]
    smoothing_key = str(smoothing or "RMA").upper().strip()
    if smoothing_key == "SMA":
        return sma(tr_values, length)
    if smoothing_key == "EMA":
        return ema(tr_values, length)
    if smoothing_key == "WMA":
        return wma(tr_values, length)
    return rma(tr_values, length)


def crossover(prev_a: float, prev_b: float, curr_a: float, curr_b: float) -> bool:
    return float(prev_a) <= float(prev_b) and float(curr_a) > float(curr_b)


def crossunder(prev_a: float, prev_b: float, curr_a: float, curr_b: float) -> bool:
    return float(prev_a) >= float(prev_b) and float(curr_a) < float(curr_b)


def ohlcv_to_series(ohlcv: List[List[float]]) -> Dict[str, List[float]]:
    timestamps = [float(row[0]) for row in ohlcv if len(row) >= 6]
    opens = [float(row[1]) for row in ohlcv if len(row) >= 6]
    highs = [float(row[2]) for row in ohlcv if len(row) >= 6]
    lows = [float(row[3]) for row in ohlcv if len(row) >= 6]
    closes = [float(row[4]) for row in ohlcv if len(row) >= 6]
    volumes = [float(row[5]) for row in ohlcv if len(row) >= 6]
    return {
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }
