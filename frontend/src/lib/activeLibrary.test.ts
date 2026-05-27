import { describe, expect, it, vi } from 'vitest';
import { activeLibraryIdRef } from '../api/client';
import {
  getActiveLibraryId,
  getActiveLibraryName,
  getActiveLibraryRole,
  setActiveLibraryId,
  setActiveLibraryRole,
  subscribeActiveLibrary,
} from './activeLibrary';

describe('activeLibrary', () => {
  it('persists the active library and notifies subscribers', () => {
    activeLibraryIdRef.current = '';
    const listener = vi.fn<(id: string) => void>();
    const unsubscribe = subscribeActiveLibrary(listener);

    setActiveLibraryId('library-a', 'Quantum i3', 'operator');

    expect(activeLibraryIdRef.current).toBe('library-a');
    expect(getActiveLibraryId()).toBe('library-a');
    expect(getActiveLibraryName()).toBe('Quantum i3');
    expect(getActiveLibraryRole()).toBe('operator');
    expect(listener).toHaveBeenCalledWith('library-a');

    unsubscribe();
  });

  it('updates just the role without clearing the active library', () => {
    setActiveLibraryId('library-b', 'Archive Library', 'viewer');

    setActiveLibraryRole('admin');

    expect(getActiveLibraryId()).toBe('library-b');
    expect(getActiveLibraryName()).toBe('Archive Library');
    expect(getActiveLibraryRole()).toBe('admin');
  });
});
