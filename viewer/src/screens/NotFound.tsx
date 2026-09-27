import { Link } from 'react-router';

export default function NotFound() {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <p className="kicker mono">Not found</p>
          <h1>There is nothing at this address</h1>
          <p className="lede">The page may have moved when the console and the viewer became one app.</p>
        </div>
      </header>
      <Link className="btn btn-primary" to="/">Go to the dashboard</Link>
    </div>
  );
}
