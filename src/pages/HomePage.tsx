import { FormEvent, useMemo, useState } from 'react';

import { useAuth } from '@/hooks/AuthContext';

type AgentResponse = {
  status?: string;
  result?: unknown;
  response?: unknown;
  output?: unknown;
  error?: string;
};

const DEFAULT_WORKER_URL =
  import.meta.env.VITE_TRENDER_WORKER_URL ||
  'http://localhost:7071/api/worker/scan';

function formatResponse(response: AgentResponse | string): string {
  if (typeof response === 'string') return response;
  return JSON.stringify(response, null, 2);
}

export function HomePage() {
  const { signOut } = useAuth();
  const [workerUrl, setWorkerUrl] = useState(DEFAULT_WORKER_URL);
  const [topic, setTopic] = useState('agentic AI');
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState<'scan' | 'range'>('scan');
  const [fromA, setFromA] = useState('');
  const [toA, setToA] = useState('');
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState('');
  const [error, setError] = useState('');

  const message = useMemo(() => {
    if (mode === 'range') {
      return `Create a trend report for ${topic} from ${fromA} to ${toA}.`;
    }
    return `Create a ${days} day trend report for ${topic}.`;
  }, [days, fromA, mode, toA, topic]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');
    setResponse('');

    try {
      const requestPayload =
        mode === 'range'
          ? {
              topic,
              start: fromA,
              end: toA,
              maxResults: 30,
              includeArxiv: true,
              includeGithub: true,
              includeWeb: true,
            }
          : {
              topic,
              days,
              maxResults: 30,
              includeArxiv: true,
              includeGithub: true,
              includeWeb: true,
            };

      const result = await fetch(workerUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestPayload),
      });

      const contentType = result.headers.get('content-type') || '';
      const responsePayload = contentType.includes('application/json')
        ? ((await result.json()) as AgentResponse)
        : await result.text();

      if (!result.ok) {
        throw new Error(formatResponse(responsePayload));
      }

      setResponse(formatResponse(responsePayload));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <div>
          <p className="text-sm uppercase tracking-[0.3em] text-cyan-300">
            Trender
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">
            Agentic trend signal explorer
          </h1>
        </div>
        <button
          onClick={() => void signOut()}
          className="rounded-full border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500 hover:text-white"
        >
          Sign out
        </button>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-6 pb-10 lg:grid-cols-[1fr_0.85fr]">
        <section className="rounded-3xl border border-slate-800 bg-slate-900/70 p-6 shadow-2xl shadow-black/20">
          <h2 className="text-xl font-semibold">Create or compare reports</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            This Rayfin-hosted app owns the authenticated product shell and
            data model. It submits structured scan jobs to a separate Python
            worker that runs discovery, GPT-5.4 analysis, bucketed trend
            scoring, and static HTML report generation.
          </p>

          <form onSubmit={submit} className="mt-6 space-y-5">
            <label className="block">
              <span className="text-sm font-medium text-slate-300">
                Trender worker endpoint
              </span>
              <input
                value={workerUrl}
                onChange={(event) => setWorkerUrl(event.target.value)}
                className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-slate-100 outline-none ring-cyan-400 transition focus:ring-2"
              />
            </label>

            <label className="block">
              <span className="text-sm font-medium text-slate-300">Topic</span>
              <input
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none ring-cyan-400 transition focus:ring-2"
              />
            </label>

            <div className="grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => setMode('scan')}
                className={`rounded-xl border px-4 py-3 text-left transition ${
                  mode === 'scan'
                    ? 'border-cyan-400 bg-cyan-400/10 text-cyan-100'
                    : 'border-slate-700 bg-slate-950 text-slate-300'
                }`}
              >
                Generate report
              </button>
              <button
                type="button"
                onClick={() => setMode('range')}
                className={`rounded-xl border px-4 py-3 text-left transition ${
                  mode === 'range'
                    ? 'border-cyan-400 bg-cyan-400/10 text-cyan-100'
                    : 'border-slate-700 bg-slate-950 text-slate-300'
                }`}
              >
                Scan explicit range
              </button>
            </div>

            {mode === 'scan' ? (
              <label className="block">
                <span className="text-sm font-medium text-slate-300">
                  Time window, days
                </span>
                <input
                  type="number"
                  min={1}
                  value={days}
                  onChange={(event) => setDays(Number(event.target.value))}
                  className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none ring-cyan-400 transition focus:ring-2"
                />
              </label>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <input
                  type="date"
                  value={fromA}
                  onChange={(event) => setFromA(event.target.value)}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none ring-cyan-400 transition focus:ring-2"
                />
                <input
                  type="date"
                  value={toA}
                  onChange={(event) => setToA(event.target.value)}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none ring-cyan-400 transition focus:ring-2"
                />
              </div>
            )}

            <div className="rounded-2xl border border-slate-800 bg-slate-950 p-4 text-sm text-slate-300">
              <div className="mb-1 text-xs uppercase tracking-[0.2em] text-slate-500">
                Agent request
              </div>
              {message}
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-xl bg-cyan-400 px-5 py-3 font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'Running Trender worker...' : 'Run'}
            </button>
          </form>
        </section>

        <section className="rounded-3xl border border-slate-800 bg-slate-900/70 p-6 shadow-2xl shadow-black/20">
          <h2 className="text-xl font-semibold">Result</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            The response includes the generated report path and top trend
            topics. In the full Rayfin deployment, job and report metadata are
            represented by the Rayfin data models under <code>rayfin/data</code>.
          </p>

          {error ? (
            <pre className="mt-6 max-h-[520px] overflow-auto whitespace-pre-wrap rounded-2xl border border-red-500/40 bg-red-950/40 p-4 text-sm text-red-100">
              {error}
            </pre>
          ) : (
            <pre className="mt-6 max-h-[520px] overflow-auto whitespace-pre-wrap rounded-2xl border border-slate-800 bg-slate-950 p-4 text-sm text-slate-200">
              {response || 'Run a request to see the agent response.'}
            </pre>
          )}
        </section>
      </main>
    </div>
  );
}
