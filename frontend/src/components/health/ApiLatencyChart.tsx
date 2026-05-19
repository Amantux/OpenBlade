import { Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';

interface ApiLatencyChartProps {
  latencies: Array<{ index: number; latency: number }>;
}

export default function ApiLatencyChart({ latencies }: ApiLatencyChartProps) {
  return (
    <div className="h-10 w-[120px]">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={latencies}>
          <XAxis dataKey="index" hide />
          <YAxis hide domain={[0, 'dataMax + 20']} />
          <Line type="monotone" dataKey="latency" stroke="#22c55e" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
