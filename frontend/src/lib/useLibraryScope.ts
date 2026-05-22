import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
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

export function useLibraryScope(): LibraryScope {
  const [searchParams] = useSearchParams();
  const [activeLibrary, setActiveLibrary] = useState<ActiveLibraryState>(() => getActiveLibraryState());
  const urlLibraryId = (searchParams.get('library') ?? '').trim();
  const libraryId = urlLibraryId || activeLibrary.id;
  const libraryName = urlLibraryId && urlLibraryId !== activeLibrary.id ? '' : activeLibrary.name;

  useEffect(() => subscribeActiveLibrary(() => setActiveLibrary(getActiveLibraryState())), []);

  useEffect(() => {
    activeLibraryIdRef.current = libraryId === 'all' ? '' : libraryId;
    return () => {
      activeLibraryIdRef.current = getActiveLibraryId();
    };
  }, [libraryId]);

  return {
    libraryId,
    libraryName,
    isAll: libraryId === '' || libraryId === 'all',
    setLibrary: (id: string, name: string) => setActiveLibraryId(id, name),
  };
}
