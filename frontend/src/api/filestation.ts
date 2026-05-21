import { rootApiRequest } from './client';

const API = '/api';

export interface UploadedFile {
  file_id: string;
  filename: string;
  size_bytes: number;
  checksum_sha256: string;
  pool_id: string | null;
  status: string;
  created_at?: string | null;
}

export interface FileListResponse {
  pool_id: string;
  files: UploadedFile[];
}

async function computeFileChecksum(file: File): Promise<string | null> {
  if (typeof globalThis.crypto === 'undefined' || typeof globalThis.crypto.subtle === 'undefined') {
    return null;
  }

  const digest = await globalThis.crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest))
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
}

function parseJsonResponse<T>(raw: string): T {
  return (raw ? JSON.parse(raw) : {}) as T;
}

export async function uploadToPool(poolId: string, file: File, onProgress?: (pct: number) => void): Promise<UploadedFile> {
  const expectedChecksum = await computeFileChecksum(file);

  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append('file', file);
    if (expectedChecksum) {
      formData.append('expected_checksum', expectedChecksum);
    }

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}/pools/${encodeURIComponent(poolId)}/upload`);
    xhr.withCredentials = true;

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    xhr.onload = () => {
      try {
        const payload = parseJsonResponse<UploadedFile | { detail?: string }>(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) {
          const uploaded = payload as UploadedFile;
          if (expectedChecksum && uploaded.checksum_sha256 !== expectedChecksum) {
            reject(new Error('Checksum verification failed after upload.'));
            return;
          }
          resolve(uploaded);
          return;
        }

        reject(new Error(typeof payload === 'object' && payload && 'detail' in payload && payload.detail ? payload.detail : `HTTP ${xhr.status}`));
      } catch (error) {
        reject(error instanceof Error ? error : new Error('Upload failed'));
      }
    };

    xhr.onerror = () => reject(new Error('Network error during upload'));
    xhr.send(formData);
  });
}

export function listFiles(poolId: string): Promise<FileListResponse> {
  return rootApiRequest<FileListResponse>(`${API}/pools/${encodeURIComponent(poolId)}/files`);
}

export async function downloadFile(fileId: string, filename?: string): Promise<void> {
  const response = await fetch(`${API}/files/${encodeURIComponent(fileId)}/download`, {
    credentials: 'include',
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename || fileId;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export function removeFile(fileId: string): Promise<{ deleted: boolean }> {
  return rootApiRequest<{ deleted: boolean }>(`${API}/files/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
  });
}

export function getFileChecksum(fileId: string): Promise<{ file_id: string; checksum_sha256: string }> {
  return rootApiRequest<{ file_id: string; checksum_sha256: string }>(`${API}/files/${encodeURIComponent(fileId)}/checksum`);
}
