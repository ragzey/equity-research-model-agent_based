(function (global) {
  const ink = "#1c1917";
  const muted = "#6d675e";
  const line = "#ddd8ce";
  const accent = "#1e3a4c";
  const market = "#9a3412";
  const paper = "#fffcf8";
  const spy = "#8a8175";

  function sizeCanvas(canvas) {
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(canvas.clientWidth || 640, 320);
    const height = Math.max(canvas.clientHeight || 240, 180);
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return { ctx, width, height };
  }

  function niceBounds(values, pad) {
    const nums = values.filter((v) => Number.isFinite(v));
    if (!nums.length) return { min: 0, max: 1 };
    let min = Math.min(...nums);
    let max = Math.max(...nums);
    if (min === max) {
      min *= 0.9;
      max *= 1.1;
      if (min === max) {
        min -= 1;
        max += 1;
      }
    }
    const span = max - min;
    return { min: min - span * pad, max: max + span * pad };
  }

  function xScale(value, min, max, left, right) {
    if (max === min) return (left + right) / 2;
    return left + ((value - min) / (max - min)) * (right - left);
  }

  function formatUsd(value) {
    if (!Number.isFinite(value)) return "";
    return (
      "$" +
      value.toLocaleString(undefined, {
        maximumFractionDigits: 0,
      })
    );
  }

  function drawValuationField(canvas, points) {
    if (!canvas || !points || !points.length) return;
    const { ctx, width, height } = sizeCanvas(canvas);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = paper;
    ctx.fillRect(0, 0, width, height);

    const values = [];
    points.forEach((point) => {
      ["value", "low", "high"].forEach((key) => {
        const number = Number(point[key]);
        if (Number.isFinite(number)) values.push(number);
      });
    });
    const { min, max } = niceBounds(values, 0.08);
    const left = 128;
    const right = width - 18;
    const top = 18;
    const rows = points.filter((p) => p.kind !== "market");
    const rowH = rows.length ? Math.min(42, (height - 56) / rows.length) : 36;
    const axisY = height - 28;

    ctx.strokeStyle = line;
    ctx.beginPath();
    ctx.moveTo(left, axisY);
    ctx.lineTo(right, axisY);
    ctx.stroke();

    const ticks = 5;
    ctx.fillStyle = muted;
    ctx.font = "11px Segoe UI, system-ui, sans-serif";
    ctx.textAlign = "center";
    for (let i = 0; i <= ticks; i += 1) {
      const value = min + ((max - min) * i) / ticks;
      const x = xScale(value, min, max, left, right);
      ctx.strokeStyle = line;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, axisY);
      ctx.stroke();
      ctx.fillStyle = muted;
      ctx.fillText(formatUsd(value), x, axisY + 14);
    }

    rows.forEach((point, index) => {
      const y = top + rowH * index + rowH * 0.55;
      ctx.fillStyle = ink;
      ctx.textAlign = "right";
      ctx.font = "12px Segoe UI, system-ui, sans-serif";
      ctx.fillText(point.label, left - 10, y + 4);
      const mid = Number(point.value);
      const low = Number.isFinite(Number(point.low)) ? Number(point.low) : mid;
      const high = Number.isFinite(Number(point.high)) ? Number(point.high) : mid;
      const x1 = xScale(low, min, max, left, right);
      const x2 = xScale(high, min, max, left, right);
      const xm = xScale(mid, min, max, left, right);
      ctx.strokeStyle = accent;
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(Math.min(x1, x2), y);
      ctx.lineTo(Math.max(x1, x2), y);
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.fillStyle = point.kind === "marker" && point.label.includes("target")
        ? market
        : accent;
      ctx.beginPath();
      ctx.arc(xm, y, 5, 0, Math.PI * 2);
      ctx.fill();
    });

    const marketPoint = points.find((p) => p.kind === "market");
    if (marketPoint && Number.isFinite(Number(marketPoint.value))) {
      const x = xScale(Number(marketPoint.value), min, max, left, right);
      ctx.strokeStyle = market;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, axisY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = market;
      ctx.textAlign = "center";
      ctx.font = "11px Segoe UI, system-ui, sans-serif";
      ctx.fillText("Market", x, top - 4);
    }
  }

  function drawIndexedPerformance(canvas, series) {
    const points = (series && series.points) || [];
    if (!canvas || points.length < 2) return;
    const { ctx, width, height } = sizeCanvas(canvas);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = paper;
    ctx.fillRect(0, 0, width, height);

    const left = 42;
    const right = width - 14;
    const top = 28;
    const bottom = height - 28;
    const stock = points.map((p) => Number(p.stock));
    const bench = points.map((p) => Number(p.benchmark));
    const { min, max } = niceBounds(stock.concat(bench), 0.08);

    const yAt = (value) =>
      bottom - ((value - min) / (max - min || 1)) * (bottom - top);
    const xAt = (index) =>
      left + (index / (points.length - 1)) * (right - left);

    ctx.strokeStyle = line;
    ctx.fillStyle = muted;
    ctx.font = "11px Segoe UI, system-ui, sans-serif";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i += 1) {
      const value = min + ((max - min) * i) / 4;
      const y = yAt(value);
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
      ctx.stroke();
      ctx.fillText(value.toFixed(0), left - 6, y + 3);
    }

    function strokeSeries(values, color) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      values.forEach((value, index) => {
        const x = xAt(index);
        const y = yAt(value);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
    strokeSeries(bench, spy);
    strokeSeries(stock, accent);

    ctx.lineWidth = 1;
    ctx.fillStyle = muted;
    ctx.textAlign = "left";
    ctx.fillText(points[0].date, left, height - 8);
    ctx.textAlign = "right";
    ctx.fillText(points[points.length - 1].date, right, height - 8);

    const ticker = series.ticker || "Stock";
    const benchLabel = series.benchmark_label || series.benchmark || "Benchmark";
    ctx.textAlign = "left";
    ctx.fillStyle = accent;
    ctx.fillRect(left, 8, 10, 3);
    ctx.fillText(ticker, left + 14, 12);
    ctx.fillStyle = spy;
    ctx.fillRect(left + 90, 8, 10, 3);
    ctx.fillText(benchLabel, left + 104, 12);
  }

  global.ResearchCharts = {
    drawValuationField,
    drawIndexedPerformance,
  };
})(window);
