export default function AtlasTab() {
  return (
    <section className="atlas-page">
      <p className="section-kicker">Location research · planned map view</p>
      <h2>Atlas</h2>
      <p className="atlas-intro">
        Atlas will connect places mentioned in extracted facts to their source documents.
        This first release records the planned workflow; it does not geocode or show a live map.
      </p>
      <div className="atlas-plan-grid">
        <article>
          <span className="atlas-step">01</span>
          <h3>Find place mentions</h3>
          <p>Use reviewed facts and their location fields to identify districts, towns, and villages.</p>
        </article>
        <article>
          <span className="atlas-step">02</span>
          <h3>Check coordinates</h3>
          <p>Resolve each place to latitude and longitude, keep its original name, and flag uncertain matches for review.</p>
        </article>
        <article>
          <span className="atlas-step">03</span>
          <h3>Explore on a map</h3>
          <p>Select a place to see related approved facts, page numbers, and the documents they came from.</p>
        </article>
      </div>
      <p className="atlas-note">The future map plan is documented in <code>SYSTEM-GUIDE.md</code>.</p>
    </section>
  );
}
