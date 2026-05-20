import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';

export default function Libraries() {
  return (
    <StubPage
      eyebrow="Overview"
      title="Multi-Library Grid"
      description="Fleet-level tenancy, cross-library health rollups, and grid operations are staged for the next build."
    >
      <Card className="bg-quantum-info">
        <div className="grid gap-3 md:grid-cols-3">
          {['Scalar-i3-LAB', 'Scalar-i3-EDGE', 'Scalar-i3-DR'].map((name, index) => (
            <div key={name} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-sm font-semibold text-slate-100">{name}</div>
              <div className="mt-2 text-sm text-slate-400">Library {index + 1} overview card placeholder.</div>
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
