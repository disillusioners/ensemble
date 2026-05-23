import {
  QueueType,
  getQueueStatusColor,
  getQueueStatusLabel,
  getQueueTypeIcon,
  getQueueTypeLabel,
} from './job-queue.model';

describe('Job Queue Model', () => {
  describe('QueueType type', () => {
    it('should have all expected type values', () => {
      const types: QueueType[] = ['fifo', 'parallel', 'defer'];
      expect(types).toHaveLength(3);
    });
  });

  describe('getQueueStatusColor', () => {
    it('should return amber-500 for paused status', () => {
      expect(getQueueStatusColor(true)).toBe('#F59E0B');
    });

    it('should return green-500 for running status', () => {
      expect(getQueueStatusColor(false)).toBe('#22C55E');
    });
  });

  describe('getQueueStatusLabel', () => {
    it('should return Paused for paused status', () => {
      expect(getQueueStatusLabel(true)).toBe('Paused');
    });

    it('should return Running for running status', () => {
      expect(getQueueStatusLabel(false)).toBe('Running');
    });
  });

  describe('getQueueTypeIcon', () => {
    it('should return view_list for fifo type', () => {
      expect(getQueueTypeIcon('fifo')).toBe('view_list');
    });

    it('should return account_tree for parallel type', () => {
      expect(getQueueTypeIcon('parallel')).toBe('account_tree');
    });

    it('should return schedule for defer type', () => {
      expect(getQueueTypeIcon('defer')).toBe('schedule');
    });
  });

  describe('getQueueTypeLabel', () => {
    it('should return FIFO for fifo type', () => {
      expect(getQueueTypeLabel('fifo')).toBe('FIFO');
    });

    it('should return Parallel for parallel type', () => {
      expect(getQueueTypeLabel('parallel')).toBe('Parallel');
    });

    it('should return Defer for defer type', () => {
      expect(getQueueTypeLabel('defer')).toBe('Defer');
    });
  });
});
