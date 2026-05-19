import { useQuery } from '@tanstack/react-query';
import { getVolumeGroups } from '../api/volumeGroups';
import ArchiveForm from '../components/archive/ArchiveForm';
import ShardWizard from '../components/archive/ShardWizard';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useInventory } from '../hooks/useInventory';

export default function Archive() {
  const inventoryQuery = useInventory();
  const volumeGroupsQuery = useQuery({ queryKey: ['volume-groups'], queryFn: getVolumeGroups, refetchInterval: 30_000 });

  if (inventoryQuery.isLoading || volumeGroupsQuery.isLoading) {
    return <Spinner />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }
  if (volumeGroupsQuery.isError) {
    return <ErrorMessage error={volumeGroupsQuery.error} onRetry={() => volumeGroupsQuery.refetch()} />;
  }

  const volumeGroups = volumeGroupsQuery.data ?? [];
  const inventory = inventoryQuery.data ?? { library_id: 'unknown', slots: [], drives: [], changer_state: 'unknown' };

  return (
    <div className="grid gap-6 xl:grid-cols-[0.9fr,1.1fr]">
      <ArchiveForm volumeGroups={volumeGroups} />
      <ShardWizard volumeGroups={volumeGroups} drives={inventory.drives} />
    </div>
  );
}
