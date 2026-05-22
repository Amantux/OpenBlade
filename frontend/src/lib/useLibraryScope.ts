import { useEffect, useState } from 'react';
import { activeLibraryIdRef } from '../api/client';
import {
  getActiveLibraryId,
  getActiveLibraryName,
  setActiveLibraryId,
  subscribeActiveLibrary,
} from './activeLibrary';

export interface LibraryScope {
  libraryId: string;
  libraryName: string;
  isAll: boolean;
  setLibrary: (id: string, name: string) => void;
}

interface ActiveLibraryState {
  id: string;
  name: string;
}

function getActiveLibraryState(): ActiveLibraryState {
  return {
    id: getActiveLibraryId(),
    name: getActiveLibraryName(),
  };
}

/**
 * Returns the current active library scope derived from localStorage.
 * Always kept in sync via the custom event bus in activeLibrary.ts.
 * Updating activeLibraryIdRef ensures AML fetch headers stay current.
 */
export function useLibraryScope(): LibraryScope {
  const [activeLibrary, setActiveLibrary] = useState<ActiveLibraryState>(() => getActiveLibraryState());

  useEffect(() => subscribeActiveLibrary(() => setActiveLibrary(getActiveLibraryState())), []);

  // Keep the mutable ref in sync so apiRequest attaches the right header
  useEffect(() => {
    activeLibraryIdRef.current = activeLibrary.id;
  }, [activeLibrary.id]);

  return {
    libraryId: activeLibrary.id,
    libraryName: activeLibrary.name,
    isAll: activeLibrary.id === '' || activeLibrary.id === 'all',
    setLibrary: (id: string, name: string) => setActiveLibraryId(id, name),
  };
}
