import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AlertTriangle, CheckCircle2, Download, FileUp, Loader2, Play, ShieldAlert } from 'lucide-react';
import './styles.css';

const API_BASE = (import.meta.env.VITE_API_BASE || window.location.origin).replace(/\/$/, '');

const actionLabel = {
  analysis: 'Iniciar analise',
  validation: 'Iniciar validacao',
  correction: 'Iniciar correcao',
  repair: 'Iniciar reparo'
};

function App() {
  const [modules, setModules] = useState([]);
  const [moduleId, setModuleId] = useState('');
  const [file, setFile] = useState(null);
  const [confirmation, setConfirmation] = useState(false);
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [upload, setUpload] = useState({ active: false, progress: 0, complete: false });
  const inputRef = useRef(null);

  const selectedModule = useMemo(
    () => modules.find((item) => item.id === moduleId),
    [modules, moduleId]
  );

  useEffect(() => {
    fetch(`${API_BASE}/api/modules`)
      .then((response) => response.json())
      .then((data) => {
        setModules(data.modules || []);
        const firstEnabled = (data.modules || []).find((item) => item.enabled);
        if (firstEnabled) setModuleId(firstEnabled.id);
      })
      .catch(() => setError('Nao foi possivel conectar ao backend.'));
  }, []);

  useEffect(() => {
    if (!job || job.status === 'completed' || job.status === 'error') return;
    const timer = setInterval(async () => {
      const response = await fetch(`${API_BASE}/api/jobs/${job.id}`);
      if (response.ok) {
        const data = await response.json();
        setJob(data.job);
      }
    }, 1200);
    return () => clearInterval(timer);
  }, [job]);

  function acceptFile(nextFile) {
    setError('');
    setJob(null);
    setUpload({ active: false, progress: 0, complete: false });
    setFile(nextFile);
  }

  function onDrop(event) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) acceptFile(dropped);
  }

  function selectModule(nextModuleId) {
    const nextModule = modules.find((item) => item.id === nextModuleId);
    if (!nextModule?.enabled) return;
    setModuleId(nextModuleId);
    setConfirmation(false);
    setJob(null);
    setError('');
    setUpload({ active: false, progress: 0, complete: false });
    setFile(null);
  }

  function createJob(formData) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('POST', `${API_BASE}/api/jobs`);
      request.responseType = 'json';

      request.upload.onprogress = (event) => {
        if (!event.lengthComputable) {
          setUpload((current) => ({ ...current, active: true }));
          return;
        }
        const progress = Math.min(100, Math.round((event.loaded / event.total) * 100));
        setUpload({ active: true, progress, complete: progress === 100 });
      };

      request.onload = () => {
        const data = request.response || {};
        if (request.status >= 200 && request.status < 300) {
          setUpload({ active: false, progress: 100, complete: true });
          resolve(data);
          return;
        }
        reject(new Error(data.detail || 'Falha ao iniciar processamento.'));
      };

      request.onerror = () => reject(new Error('Nao foi possivel enviar o arquivo ao backend.'));
      request.onabort = () => reject(new Error('Upload cancelado.'));
      request.send(formData);
    });
  }

  async function submit() {
    if (!selectedModule || !file) {
      setError('Selecione um modulo e um arquivo.');
      return;
    }
    if (selectedModule.requires_confirmation && !confirmation) {
      setError('Confirme o aviso de risco antes de iniciar o reparo.');
      return;
    }

    setError('');
    setUpload({ active: true, progress: 0, complete: false });
    const formData = new FormData();
    formData.append('module_id', selectedModule.id);
    formData.append('confirmation', String(confirmation));
    formData.append('file', file);

    let data;
    try {
      data = await createJob(formData);
    } catch (uploadError) {
      setError(uploadError.message);
      setUpload({ active: false, progress: 0, complete: false });
      return;
    }

    setJob({
      id: data.job_id,
      status: 'pending',
      logs: ['Job criado.'],
      module_id: selectedModule.id,
      original_filename: file.name
    });
  }

  const busy = job?.status === 'pending' || job?.status === 'processing';
  const uploading = upload.active;
  const canRun = selectedModule?.enabled && file && !busy && !uploading;
  const uploadStatus = upload.active
    ? upload.progress >= 100
      ? 'Upload completo. Criando job no servidor...'
      : `Enviando arquivo: ${upload.progress}%`
    : upload.complete
      ? 'Upload completo. Processamento iniciado.'
      : file
        ? 'Arquivo pronto para envio.'
        : '';

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p>Analise, validacao e reparo modular de arquivos.</p>
        </div>
        <div className="status-pill">API {API_BASE}</div>
      </header>

      <nav className="module-tabs" aria-label="Modulos">
        {modules.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`module-tab ${item.id === moduleId ? 'active' : ''}`}
            disabled={!item.enabled}
            onClick={() => selectModule(item.id)}
            title={item.disabled_reason || item.description}
          >
            <span>{item.name}</span>
          </button>
        ))}
      </nav>

      <section className="workspace">
        <section className="module-summary">
          {selectedModule && (
            <div className="module-info">
              <div className="section-label">Modulo selecionado</div>
              <h1>{selectedModule.name}</h1>
              <p>{selectedModule.description}</p>
              <div className="meta-row">
                <span>{selectedModule.operation_type}</span>
                <span>{selectedModule.accepted_extensions.join(', ')}</span>
              </div>
              {!selectedModule.enabled && <p className="warning">{selectedModule.disabled_reason}</p>}
            </div>
          )}
        </section>

        <section className="upload-area">
          <div
            className={`dropzone ${dragging ? 'dragging' : ''}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
          >
            <FileUp size={40} />
            <h1>{file ? file.name : 'Envie um arquivo para iniciar'}</h1>
            <p>
              {selectedModule
                ? `Aceito: ${selectedModule.accepted_extensions.join(', ')}`
                : 'Selecione um modulo'}
            </p>
            <button type="button" className="secondary">Selecionar arquivo</button>
            <input
              ref={inputRef}
              type="file"
              accept={selectedModule?.accepted_extensions.join(',')}
              hidden
              onChange={(event) => {
                const nextFile = event.target.files?.[0];
                if (nextFile) acceptFile(nextFile);
              }}
            />
          </div>

          {file && (
            <div className="upload-progress" aria-live="polite">
              <div className="progress-row">
                <span>{uploadStatus}</span>
                <span>{upload.complete ? '100%' : `${upload.progress}%`}</span>
              </div>
              <div className="progress-track" aria-hidden="true">
                <div
                  className={`progress-fill ${upload.active ? 'active' : ''}`}
                  style={{ width: `${upload.complete ? 100 : upload.progress}%` }}
                />
              </div>
            </div>
          )}

          {selectedModule?.requires_confirmation && (
            <label className="risk-box">
              <input
                type="checkbox"
                checked={confirmation}
                onChange={(event) => setConfirmation(event.target.checked)}
              />
              <ShieldAlert size={20} />
              <span>Confirmo que o reparo sera feito em copia isolada e que um backup sera criado antes da operacao.</span>
            </label>
          )}

          {error && (
            <div className="alert">
              <AlertTriangle size={18} />
              {error}
            </div>
          )}

          <button className="primary" disabled={!canRun} onClick={submit}>
            {busy || uploading ? <Loader2 className="spin" size={18} /> : <Play size={18} />}
            {uploading ? 'Enviando arquivo' : selectedModule ? actionLabel[selectedModule.operation_type] : 'Iniciar'}
          </button>
        </section>

        <section className="result-panel">
          <h2>Status</h2>
          {!job && <p className="muted">Nenhum processamento iniciado.</p>}
          {job && (
            <>
              <div className={`job-status ${job.status}`}>
                {job.status === 'completed' ? <CheckCircle2 size={18} /> : <Loader2 className={busy ? 'spin' : ''} size={18} />}
                {job.status}
              </div>
              {job.result && <p className="result">{job.result}</p>}
              {job.error && <p className="warning">{job.error}</p>}
              {busy && <p className="muted">Processando arquivo. Os downloads aparecem quando a execucao terminar.</p>}

              <div className="downloads">
                {job.has_report && (
                  <a href={`${API_BASE}/api/jobs/${job.id}/report`}>
                    <Download size={16} /> Baixar relatorio
                  </a>
                )}
                {job.has_output && (
                  <a href={`${API_BASE}/api/jobs/${job.id}/output`}>
                    <Download size={16} /> Baixar arquivo
                  </a>
                )}
              </div>
            </>
          )}
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
