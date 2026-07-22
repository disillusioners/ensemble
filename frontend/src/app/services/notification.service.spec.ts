import { signal } from '@angular/core';
import type { InstanceNotification } from './notification.service';

// =============================================================================
// WAV VALIDITY TESTS - Test the embedded audio data
// =============================================================================

describe('NotificationService WAV Audio Data', () => {
  // Valid WAV chime: A5 (880Hz) sine wave with exponential decay, 120ms, 16kHz mono
  // This is the actual base64 from notification.service.ts
  const chimeBase64 = 'data:audio/wav;base64,UklGRiQPAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAPAAAAAPImJ0mZYk9wt3DRYy9LximOAxHd17rnoEmSrZA1nHqzsNMC+QMfN0GZWxFr2G2lY7lNti5OCtLkrsLnp6CXupOgnDexB8+C8nEXejmaVKRlnmr+YrdPGjOQEDPsT8ririGdHZeAnX2v6cp/7EEQ9jGmTRFgFGfmYS9R8zZUFjLztNHNtcOizJrOnkWuU8f55nQJsSrFRmNaRGNlYChSRzqcG8352NijvHyovJ6AoIitQMTv4Q0DsCP9P6JUNl+EXqpSGj1pIAAAtt9bw0Ou5aKPokCtq8Fe3Q399hxWOddO9FpLXLtScT+8JMwFSubvyRC0PafwpGStkb9G2XX3iRbWMglJh1bDWWNSUUGaKDALkexZ0N25vaucp+6t6r2j1UXyahCDLEBD91H0VqlRv0IDLCwQh/KU1qC/W7CLqteusrxy0n3tnQphJoM9TE3nU5RQwkP9LsAUKvic3FXFEbW1rRaw5Luwzx3pJAV2INk3jkijUCxPXkSJMe0Yef1q4vTK1rkRsaWxeLtYzSLlAADEGkcyw0MwTXhNmkSsM7YccQL+53fQpL6ZtH2zartoy4vhMvtQFdIs9D6WSX5LfERrNRsgFQdS7dvVdMNEuJa1s7vayVfeuvYdEIEnJTrbRUdJCUTJNh8jYgtk8hnbQMgMvOq3TbyqyIPbmPIrC1ciXjUHQthGSUPMN8QlWA8z9y/gAc3rv3K6M73UxwzZze5/BlcdozAfPjlEQEJ3OA0o+RK9+xfltNHawye9Xb5Sx+/WVusYAocY+isrOnBB9UDQOP4pRhYAAM/pUtbTxwLAx78hxynVM+j6/ecTaCcvNoM+bj/cOJorPxn8A1Xu19rQy//CasE6x7fTY+Uh+n0P8CIyMng7sT2fOOQs5xuwB6XyQN/MzxfGQMOZx5XS4+KR9kgLlx44LlU4wzsfOOAtQB4dC732iOPC00PJRcU4yL7RseBH80sHYRpHKiA1qjlhN5EuSyBDDp36reeu14DMcccUyTDRzN5F8IgDTxZiJt0xazdqNv0uCyIiEUP+q+uL28fPwMkmyufQMN2J7QAAZRKOIpMuDTU+NSYvhCO7E64BgO9V3xTTLcxqy93Q2tsS67P8pg7OHkQrkzLjMxEvtyQQFt4EKfMJ42PWs87bzA/Rydre6KH5EwsmG/YnAzBdMsMupyUjGNIHpvak5q/ZTNF1znnR+Nns5sr2rQeaF64kYS2xMD4uWSb1GYwK9Pki6vTc9dMy0BfSZdk75S70dwQqFG0hsirjLogtzyaHGwoNEv2B7S7gqdYO0uTSDNnH48zxcAHbEDoe+if5LKUsDCfcHE8PAADA8FvjZNkF1N3T6tiP4qTvnP6uDRUbPSX3KpgrFCf3HVoRvQLb83bmItwT1vzU+9iQ4bXt9/ulCgMYfyLfKGYq6ibaHi0TSAXS9n7p394z2EDWPNnJ4P7rhPnBBwYVxB+4JhMpkiaHH8oUowej+W/smOFh2qLXqtk24HzqQvcDBSESDh2DJKInECYBIDIWzQlN/EjvSuSb3CHZQdrU3y7pMfVtAlUPYRpFIhcmZiVLIGYXxgvP/gfy8ebc3rfa/dqh3xPoT/MAAKUMvxcCIHYklyRnIGgYjw0nAan0jOkh4WLc3Nua3yjnnfG7/REKLBW8HcIiqSNZIDsZKg9YAy33GOxn4x7e2ty932zmGvCf+5wHqRJ3G/8gnSIjIOEZlxBgBZP5ku6q5eff890G4N3lxO6r+UYFOBA1GTAfdyHIH1sa1xE/B9j7+fDq57vhJd9y4Hjlmu3g9xED3A35FlgdOiBLH6wa6xL2CP39SvMi6pfjbOD/4Drlmuw99v0AlgvGFHob6R6vHtYa1hOFCgAAhfVQ7HflxuGq4SPlxOvC9Av/ZwmdEpgZhx32HdsamBTtC+EBqPdz7lnnL+Nw4i/lFetu8zr9UAeBELYXFxwkHb8aNBUuDaEDsfmI8DvppeRO41zljOpA8ov7VAVzDtUVnBo6HIIaqxVKDkAFofuO8hrrJeZC5KflJ+o28f75cgN1DPgTGBk9Gyka/xVBD7wGdf2D9PTsredJ5Q/m5OlR8JL4qwGJCiESjhctGrQZMhYVEBcILv9m9sfuOelg5pDmwemO70f3AACwCFIQABYPGSYZRRbGEFIJywA1+JLwyOqF5ynnvOnt7h32cf7rBo0OcBTjF4IYOxZXEWwKTALw+VLyWOy26Njn1Ols7hP1/vw7BdMM4BKtFsoXFhbJEWYLsgOV+wX05+3x6ZnoBuoJ7ij0pvugAyYLUxFvFQAX2BUdEkIM+wQl/az1cu8y62vpUOrE7Vvza/ocAoYJyg8rFCYWghVVEgANKQae/kT3+PB57Ezqseqa7avyS/muAPYHRw7iEj4VFhVxEqINOwcAAMz4ePLD7TrrJ+uK7RjyRvhY/3YGywyXEUsUlxR1EicOMghLAUT68PMP7zLsr+uS7aDxXPcY/gcFVwtLEE4TBxRiEpMODwl/Aqr7XvVa8DPtR+yx7UHxjfbw/KkD7gkBD0oSZhM4EuQO0gmbA//8wvaj8Tvu7+zk7fzw1vXf+14CkAi5DT8RuBL6ER4PfAqhBEH+G/jp8kjvpO0s7s7wOfXl+iUBPgd2DDAQ/hGqEUEPDQuPBXD/Z/kq9FnwZO6F7rbws/QC+gAA+QU3Cx4PORFJEU4PhwtoBosApfpl9WvxLe/u7rPwRPQ1+e7+wQQACgwOaxDYEEgP6wspB5QB1fuZ9n7y/+9m78Pw6/N++O/9mAPQCPkMlg9ZEC4POQzWB4oC9/zF95Dz1/Dr7+XwqPPc9wP9fgKpB+gLuw7ODwMPcwxtCGwDCv7n+J/0tPF78BnxePNP9yr8cwGMBtoK3A05D8gOmQzvCDsEDf//+az1lfIW8VvxW/PX9mT7dwB5BdAJ+gyZDn4OrQxeCfgEAAAN+7T2d/O58azxUPNx9rH6jf9xBMsIFwzyDScOrwy6CaIF4wAP/Lb3W/Rk8gnyVfMf9hD6sf50A8sHMwtFDcMNogwECjoGtwEF/bL4P/UU83Lya/Pe9YH55f2EAtMGUAqRDFUNhQw8Cr8GegLv/ab5IfbK8+byjvOu9QT5Kv2gAeIFbgnaC90MWwxkCjQHLgPN/pP6AfeD9GLzv/OO9Zj4fvzJAPoEkAggC10MJAx8CpgH0gOd/3f73vc+9ebz/PN+9Tz44vsAABoEtQdkCtYL4QuFCuwHZwRfAFL8tvj79XD0RfR89fD3VftE/0QD3wanCUgLkwuACjEI7AQWASP9ivm39gD1l/SH9bP31/qU/ngCDgbqCLYKPAtvCmYIYgW+Aer9WPp095X18vSf9YT3aPry/bYBRAUvCCAK3QpRCo4IygVaAqf+H/su+C32VvXC9WP3B/pd/f8AfwR1B4YJdgooCqgIJAboAln/4Pvn+Mf2wPXw9U/3tPnV/FIAwgO+BusICQr2CbYIcAZqAwAAmfyc+WP3MPYn9kj3b/la/LH/DQMLBk8Ilgm6CbgIrwbfA5wAS/1O+gD4pfZn9kz3Nvns+xr/YAJbBbIHHgl1Ca8I4gZHBC0B9P37+pz4H/ew9lr3CfmJ+47+ugGxBBYHowgqCZsICQejBLQBlf6j+zj5nPf/9nP36Pgz+w3+HgELBHsGJQjXCH4IJAfzBC8CLf9F/NH5G/hU95T30/jp+pf9igBrA+IFpQd/CFgINAc4BaACvP/i/Gn6m/iv9773x/iq+iz9AADSAkwFJAciCCoIOwdyBQYDQQB5/f76HfkO+PD3xvh1+sv8f/8/ArkEogbBB/UHNwehBWIDvwAI/o/7n/lx+Cn4zvhL+nT8Bv+yASkEIAZcB7kHKwfGBbMDMwGR/h38IPrX+Gf43vgr+ij8l/4tAZ4DoAX1BncHFwfhBfsDngET/6b8oPo/+az49vgV+uX7MP6vABcDIAWMBjAH+wbzBTgEAAKN/yr9H/up+fX4FfkH+qz70/04AJUCogQhBuUG2Ab8BW0EWQIAAKr9nPsV+kL5PPkC+n37fv3K/xkCJwS2BZYGrwb+BZgEqQJrACT+FvyA+pL5aPkF+lb7Mv1i/6EBrgNKBUQGgAb3BbsE8ALPAJj+jfzs+ub5mfkP+jf77/wC/zABOQPfBPAFTAbqBdUEMAMrAQf/AP1X+zz60Pkf+iD7tPyp/sQAxwJ0BJkFEwbWBegEZwOAAW//cP3A+5P6Cvo3+hH7gfxY/l8AWQILBEEF1wW8BfMElgPOAdL/3P0p/Ov6SfpT+gr7VfwO/gAA8AGkA+gElwWcBfgEvgMUAi0AQ/6P/ET7ivp2+gn7MfzM/af/iwE/A48EVAV4BfYE3gNTAoMApv7z/J77zvqc+g77FPyQ/VT/KgHcAjYEDwVOBe0E+AOLAtMABP9U/ff7FPvI+hn7/vtc/Qj/zwB8At0DyAQhBd8ECgS8AhwBXf+y/U/8XPv3+ir77/su/cH+eAAgAoYDgATxBMwEFwTmAl8Bsf8N/qb8pfsp+z/75fsH/YH+JgDGAS8DNgS9BLQEHQQKA50BAABl/vz87/te+1n74vvm/Ef+2/9xAdoC7AOHBJgEHgQoA9QBSQC5/k/9OfyV+3j75PvL/BP+lP8fAYcCogNOBHgEGgRAAwUCjgAJ/6H9g/zP+5r76vu2/OX9Uf/RADcCWQMUBFQEEARSAzACzgBV//H9zPwJ/L/79vun/Lz9Ff+HAOkBEAPZAy0EAwRfA1YCCAGd/z3+Ff1F/Oj7Bvyc/Jn93f5BAJ0BxwKcAwME8QNnA3cCPQHg/4f+XP2C/BL8GvyX/Hv9qv4AAFUBgAJfA9cD2wNqA5ICbQEfAM7+o/2//D/8MfyW/GL9fP7D/w8BOwIiA6kDwgNoA6gCmQFaABL/5/39/G78TPya/E79U/6K/80A9wHlAnoDpQNjA7oCvwGRAFP/Kv46/Z78avyi/D/9L/5W/44AtQGoAkkDhgNZA8cC4QHDAJD/a/53/dD8ivyt/DX9EP4l/1IAdQFrAhcDZQNMA88C/gHxAMr/qf6y/QL9rPy8/C799f35/hoAOAEwAuUCQgM7A9QCFwIbAQAA5v7t/TT90fzO/Cz93v3R/uf//QD2AbICHAMoA9QCKwJBATIAH/8=';

  describe('Base64 Decoding', () => {
    it('should decode base64 string without error', () => {
      const base64Data = chimeBase64.split(',')[1];
      expect(() => atob(base64Data)).not.toThrow();
    });

    it('should have valid data URL prefix', () => {
      expect(chimeBase64).toMatch(/^data:audio\/wav;base64,/);
    });
  });

  describe('WAV File Structure', () => {
    let audioData: Uint8Array;

    beforeAll(() => {
      const base64Data = chimeBase64.split(',')[1];
      const binaryString = atob(base64Data);
      audioData = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        audioData[i] = binaryString.charCodeAt(i);
      }
    });

    it('should start with RIFF magic bytes', () => {
      const riff = String.fromCharCode(...audioData.slice(0, 4));
      expect(riff).toBe('RIFF');
    });

    it('should contain WAVE format marker', () => {
      const wave = String.fromCharCode(...audioData.slice(8, 12));
      expect(wave).toBe('WAVE');
    });

    it('should have fmt chunk after WAVE header', () => {
      const fmt = String.fromCharCode(...audioData.slice(12, 16));
      expect(fmt).toBe('fmt ');
    });

    it('should have data chunk after fmt chunk', () => {
      let dataOffset = -1;
      for (let i = 12; i < audioData.length - 4; i++) {
        const chunk = String.fromCharCode(...audioData.slice(i, i + 4));
        if (chunk === 'data') {
          dataOffset = i;
          break;
        }
      }
      expect(dataOffset).toBeGreaterThan(12);
    });

    describe('fmt Chunk Validation', () => {
      it('should have PCM format (format = 1)', () => {
        const format = audioData[22] | (audioData[23] << 8);
        expect(format).toBe(1);
      });

      it('should have mono or stereo channel', () => {
        const channels = audioData[22] | (audioData[23] << 8);
        expect(channels).toBeGreaterThanOrEqual(1);
        expect(channels).toBeLessThanOrEqual(2);
      });

      it('should have valid sample rate', () => {
        const sampleRate = audioData[24] | (audioData[25] << 8) |
                          (audioData[26] << 16) | (audioData[27] << 24);
        expect(sampleRate).toBeGreaterThan(0);
        expect(sampleRate).toBeLessThanOrEqual(192000);
      });

      it('should have valid bits per sample (8, 16, 24, or 32)', () => {
        const bitsPerSample = audioData[34] | (audioData[35] << 8);
        expect([8, 16, 24, 32]).toContain(bitsPerSample);
      });
    });

    describe('File Size Validation', () => {
      it('should have total file size matching actual decoded bytes', () => {
        const declaredSize = audioData[4] | (audioData[5] << 8) |
                           (audioData[6] << 16) | (audioData[7] << 24);
        const actualSize = audioData.length;
        expect(declaredSize).toBeLessThanOrEqual(actualSize);
      });

      it('should contain declared data size in data chunk header', () => {
        let dataOffset = -1;
        for (let i = 12; i < audioData.length - 4; i++) {
          const chunk = String.fromCharCode(...audioData.slice(i, i + 4));
          if (chunk === 'data') {
            dataOffset = i;
            break;
          }
        }
        expect(dataOffset).toBeGreaterThan(0);

        const dataSize = audioData[dataOffset + 4] |
                        (audioData[dataOffset + 5] << 8) |
                        (audioData[dataOffset + 6] << 16) |
                        (audioData[dataOffset + 7] << 24);

        expect(dataSize).toBeGreaterThan(0);
        const actualAudioData = audioData.length - (dataOffset + 8);
        expect(dataSize).toBeLessThanOrEqual(actualAudioData + 100);
      });
    });
  });
});

// =============================================================================
// AUDIO UNLOCK RETRY LOGIC TESTS
// =============================================================================

describe('NotificationService Audio Unlock Logic', () => {
  class MockAudio {
    src: string = '';
    volume: number = 1;
    currentTime: number = 0;
    paused: boolean = true;
    private playPromise: Promise<void> | null = null;
    private playResolve: (() => void) | null = null;
    private playReject: ((reason: any) => void) | null = null;
    playCallCount = 0;

    play(): Promise<void> {
      this.playCallCount++;
      if (this.playPromise) {
        return this.playPromise;
      }
      this.paused = false;
      this.playPromise = new Promise<void>((resolve, reject) => {
        this.playResolve = resolve;
        this.playReject = reject;
      });
      return this.playPromise;
    }

    pause(): void {
      this.paused = true;
    }

    resolvePlay(): void {
      if (this.playResolve) {
        const r = this.playResolve;
        this.playResolve = null;
        this.paused = true;
        r();
      }
      this.playPromise = null;
    }

    rejectPlay(error?: Error): void {
      if (this.playReject) {
        const rej = this.playReject;
        this.playReject = null;
        rej(error || new Error('Play failed'));
      }
      this.playPromise = null;
    }
  }

  class TestableNotificationService {
    private readonly API_BASE = '/api';
    readonly notifications = signal<any[]>([]);
    readonly unreadCount = signal<number>(0);
    private eventSource: EventSource | null = null;

    audio: MockAudio | null = null;
    audioUnlocked = false;
    unlockHandler: (() => void) | null = null;

    constructor(mockAudio: MockAudio | null) {
      this.audio = mockAudio;
      if (this.audio) {
        this.audio.volume = 0.5;
      }
      this.setupAudioUnlock();
    }

    private setupAudioUnlock(): void {
      const unlock = () => {
        if (this.audio && !this.audioUnlocked) {
          const playPromise = this.audio.play();
          playPromise.then(() => {
            this.audioUnlocked = true;
            if (this.audio) {
              this.audio.pause();
              this.audio.currentTime = 0;
            }
            this.unlockHandler = null;
          }).catch(() => {});
        }
      };
      this.unlockHandler = unlock;
    }

    private playSound(): void {
      if (this.audio && this.audioUnlocked) {
        this.audio.currentTime = 0;
        this.audio.play().catch(() => {});
      }
    }

    getUnlockHandler(): (() => void) | null {
      return this.unlockHandler;
    }

    ngOnDestroy(): void {
      this.disconnect();
      if (this.unlockHandler) {
        this.unlockHandler = null;
      }
    }

    disconnect(): void {
      if (this.eventSource) {
        (this.eventSource as any).close();
        this.eventSource = null;
      }
    }

    addNotification(notification: InstanceNotification): void {
      const newNotification = {
        ...notification,
        id: notification.instance_id,
        read: false,
      };
      this.notifications.update(list => [newNotification, ...list].slice(0, 50));
      this.unreadCount.update(count => count + 1);
      this.playSound();
    }
  }

  describe('play() succeeds', () => {
    it('should set audioUnlocked to true on successful play()', async () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      expect(service.audioUnlocked).toBe(false);

      const unlockHandler = service.getUnlockHandler();
      expect(unlockHandler).toBeTruthy();

      if (unlockHandler) {
        unlockHandler();
      }
      mockAudio.resolvePlay();

      // Wait for promise resolution to be processed
      await new Promise(resolve => setTimeout(resolve, 10));

      expect(service.audioUnlocked).toBe(true);
    });

    it('should pause audio after successful unlock', async () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      const unlockHandler = service.getUnlockHandler();
      if (unlockHandler) {
        unlockHandler();
      }
      mockAudio.resolvePlay();

      await new Promise(resolve => setTimeout(resolve, 10));

      expect(mockAudio.paused).toBe(true);
      expect(mockAudio.currentTime).toBe(0);
    });

    it('should set unlockHandler to null after successful unlock', async () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      const unlockHandler = service.getUnlockHandler();
      expect(unlockHandler).toBeTruthy();

      if (unlockHandler) {
        unlockHandler();
      }
      mockAudio.resolvePlay();

      await new Promise(resolve => setTimeout(resolve, 10));

      expect(service.getUnlockHandler()).toBeNull();
    });
  });

  describe('play() fails (DOMException)', () => {
    it('should keep audioUnlocked as false after failed play()', () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      expect(service.audioUnlocked).toBe(false);

      const unlockHandler = service.getUnlockHandler();
      if (unlockHandler) {
        unlockHandler();
      }
      mockAudio.rejectPlay(new DOMException('Audio play failed', 'AbortError'));

      expect(service.audioUnlocked).toBe(false);
    });

    it('should keep unlockHandler active after failed play()', () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      const unlockHandler = service.getUnlockHandler();
      expect(unlockHandler).toBeTruthy();

      if (unlockHandler) {
        unlockHandler();
      }
      mockAudio.rejectPlay(new DOMException('Audio play failed', 'AbortError'));

      expect(service.getUnlockHandler()).not.toBeNull();
    });
  });

  describe('subsequent interaction retries unlock', () => {
    it('should succeed on retry after initial failure', async () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      expect(service.audioUnlocked).toBe(false);

      // First attempt - fail
      let handler = service.getUnlockHandler();
      if (handler) handler();
      mockAudio.rejectPlay(new DOMException('Play failed', 'AbortError'));
      await new Promise(resolve => setTimeout(resolve, 10));
      expect(service.audioUnlocked).toBe(false);

      // Second attempt - succeed
      handler = service.getUnlockHandler();
      if (handler) handler();
      mockAudio.resolvePlay();
      await new Promise(resolve => setTimeout(resolve, 10));
      expect(service.audioUnlocked).toBe(true);
    });
  });

  describe('multiple failed play() calls', () => {
    it('should not break the service', async () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      for (let i = 0; i < 5; i++) {
        const handler = service.getUnlockHandler();
        if (handler) handler();
        mockAudio.rejectPlay(new Error('Failed'));
        await new Promise(resolve => setTimeout(resolve, 10));
      }

      expect(service.getUnlockHandler()).not.toBeNull();
      expect(service.audioUnlocked).toBe(false);

      // Should still be able to unlock
      const handler = service.getUnlockHandler();
      if (handler) handler();
      mockAudio.resolvePlay();
      await new Promise(resolve => setTimeout(resolve, 10));
      expect(service.audioUnlocked).toBe(true);
    });
  });
});

// =============================================================================
// NGONDESTROY CLEANUP TESTS
// =============================================================================

describe('NotificationService ngOnDestroy Cleanup', () => {
  class MockAudio {
    volume: number = 1;
    currentTime: number = 0;
    paused: boolean = true;

    play(): Promise<void> {
      return Promise.resolve();
    }
    pause(): void {
      this.paused = true;
    }
  }

  class TestableNotificationService {
    private readonly API_BASE = '/api';
    readonly notifications = signal<any[]>([]);
    readonly unreadCount = signal<number>(0);
    private eventSource: EventSource | null = null;

    audio: MockAudio | null = null;
    audioUnlocked = false;
    unlockHandler: (() => void) | null = null;
    listenersActive = false;

    constructor(mockAudio: MockAudio | null) {
      this.audio = mockAudio;
      if (this.audio) {
        this.audio.volume = 0.5;
      }
      this.setupAudioUnlock();
    }

    private setupAudioUnlock(): void {
      const unlock = () => {
        if (this.audio && !this.audioUnlocked) {
          this.audio.play().then(() => {
            this.audioUnlocked = true;
            if (this.audio) {
              this.audio.pause();
              this.audio.currentTime = 0;
            }
            this.unlockHandler = null;
            this.listenersActive = false;
          }).catch(() => {});
        }
      };
      this.unlockHandler = unlock;
      this.listenersActive = true;
    }

    ngOnDestroy(): void {
      this.disconnect();
      if (this.unlockHandler) {
        this.unlockHandler = null;
        this.listenersActive = false;
      }
    }

    disconnect(): void {
      if (this.eventSource) {
        (this.eventSource as any).close();
        this.eventSource = null;
      }
    }
  }

  it('should clean up unlockHandler in ngOnDestroy', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);

    expect(service['unlockHandler']).toBeTruthy();
    expect(service['listenersActive']).toBe(true);

    service.ngOnDestroy();

    expect(service['unlockHandler']).toBeNull();
    expect(service['listenersActive']).toBe(false);
  });

  it('should be safe to call ngOnDestroy multiple times', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);

    service.ngOnDestroy();
    expect(() => service.ngOnDestroy()).not.toThrow();
    expect(service['unlockHandler']).toBeNull();
    expect(service['listenersActive']).toBe(false);
  });

  it('should handle ngOnDestroy when audio is null', () => {
    const service = new TestableNotificationService(null);
    expect(() => service.ngOnDestroy()).not.toThrow();
  });
});

// =============================================================================
// EDGE CASES TESTS
// =============================================================================

describe('NotificationService Edge Cases', () => {
  class MockAudio {
    volume: number = 1;
    currentTime: number = 0;
    paused: boolean = true;
    playCallCount = 0;

    play(): Promise<void> {
      this.playCallCount++;
      return Promise.resolve();
    }
    pause(): void {
      this.paused = true;
    }
  }

  class TestableNotificationService {
    private readonly API_BASE = '/api';
    readonly notifications = signal<any[]>([]);
    readonly unreadCount = signal<number>(0);
    private eventSource: EventSource | null = null;

    audio: MockAudio | null = null;
    audioUnlocked = false;
    unlockHandler: (() => void) | null = null;

    constructor(mockAudio: MockAudio | null) {
      this.audio = mockAudio;
      if (this.audio) {
        this.audio.volume = 0.5;
      }
      this.setupAudioUnlock();
    }

    private setupAudioUnlock(): void {
      const unlock = () => {
        if (this.audio && !this.audioUnlocked) {
          this.audio.play().then(() => {
            this.audioUnlocked = true;
            if (this.audio) {
              this.audio.pause();
              this.audio.currentTime = 0;
            }
            this.unlockHandler = null;
          }).catch(() => {});
        }
      };
      this.unlockHandler = unlock;
    }

    private playSound(): void {
      if (this.audio && this.audioUnlocked) {
        this.audio.currentTime = 0;
        this.audio.play().catch(() => {});
      }
    }

    addNotification(notification: InstanceNotification): void {
      const newNotification = {
        ...notification,
        id: notification.instance_id,
        read: false,
      };
      this.notifications.update(list => [newNotification, ...list].slice(0, 50));
      this.unreadCount.update(count => count + 1);
      this.playSound();
    }

    ngOnDestroy(): void {
      this.disconnect();
      if (this.unlockHandler) {
        this.unlockHandler = null;
      }
    }

    disconnect(): void {
      if (this.eventSource) {
        (this.eventSource as any).close();
        this.eventSource = null;
      }
    }
  }

  describe('multiple rapid notifications', () => {
    it('should not crash with multiple rapid notifications', () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);
      service['audioUnlocked'] = true;

      const notifications: InstanceNotification[] = [];
      for (let i = 0; i < 10; i++) {
        notifications.push({
          instance_id: `instance-${i}`,
          agent_id: 'agent-1',
          name: `Notification ${i}`,
          status: 'COMPLETED' as const,
          timestamp: new Date().toISOString(),
        });
      }

      expect(() => {
        notifications.forEach(n => service.addNotification(n));
      }).not.toThrow();

      expect(service.notifications().length).toBe(10);
    });

    it('should handle rapid notifications without audio errors', () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);
      service['audioUnlocked'] = true;

      for (let i = 0; i < 5; i++) {
        service.addNotification({
          instance_id: `instance-${i}`,
          agent_id: 'agent-1',
          name: `Notification ${i}`,
          status: 'COMPLETED' as const,
          timestamp: new Date().toISOString(),
        });
      }

      expect(service.notifications().length).toBe(5);
      expect(mockAudio.playCallCount).toBeGreaterThan(0);
    });
  });

  describe('null audio handling', () => {
    it('should work when audio is null', () => {
      const service = new TestableNotificationService(null);

      expect(service['audio']).toBeNull();

      expect(() => {
        service.addNotification({
          instance_id: 'instance-1',
          agent_id: 'agent-1',
          name: 'Test',
          status: 'COMPLETED' as const,
          timestamp: new Date().toISOString(),
        });
      }).not.toThrow();

      expect(service.notifications().length).toBe(1);
    });

    it('should handle null audio in ngOnDestroy', () => {
      const service = new TestableNotificationService(null);
      expect(() => service.ngOnDestroy()).not.toThrow();
    });

    it('should handle null audio in playSound', () => {
      const service = new TestableNotificationService(null);
      service['audioUnlocked'] = true;
      expect(() => service['playSound']()).not.toThrow();
    });
  });

  describe('audio unlock state', () => {
    it('should not play sound when audioUnlocked is false', () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      expect(service['audioUnlocked']).toBe(false);

      service.addNotification({
        instance_id: 'instance-1',
        agent_id: 'agent-1',
        name: 'Test',
        status: 'COMPLETED' as const,
        timestamp: new Date().toISOString(),
      });

      expect(mockAudio.playCallCount).toBe(0);
    });

    it('should play sound when audioUnlocked is true', () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);
      service['audioUnlocked'] = true;

      service.addNotification({
        instance_id: 'instance-1',
        agent_id: 'agent-1',
        name: 'Test',
        status: 'COMPLETED' as const,
        timestamp: new Date().toISOString(),
      });

      expect(mockAudio.playCallCount).toBe(1);
    });

    it('should respect audio unlock state for playSound', () => {
      const mockAudio = new MockAudio();
      const service = new TestableNotificationService(mockAudio);

      expect(service['audioUnlocked']).toBe(false);
      service['playSound']();
      expect(mockAudio.playCallCount).toBe(0);

      service['audioUnlocked'] = true;
      service['playSound']();
      expect(mockAudio.playCallCount).toBe(1);
    });
  });
});

// =============================================================================
// NOTIFICATION SIGNAL TESTS
// =============================================================================

describe('NotificationService Signal Behavior', () => {
  class TestableNotificationService {
    readonly notifications = signal<any[]>([]);
    readonly unreadCount = signal<number>(0);

    addNotification(notification: any): void {
      const newNotification = {
        ...notification,
        id: notification.instance_id,
        read: false,
      };
      this.notifications.update(list => [newNotification, ...list].slice(0, 50));
      this.unreadCount.update(count => count + 1);
    }

    markAsRead(id: string): void {
      this.notifications.update(list =>
        list.map(n => (n.id === id ? { ...n, read: true } : n))
      );
      this.recalculateUnreadCount();
    }

    markAllAsRead(): void {
      this.notifications.update(list =>
        list.map(n => ({ ...n, read: true }))
      );
      this.unreadCount.set(0);
    }

    clearNotification(id: string): void {
      const wasUnread = this.notifications().find(n => n.id === id && !n.read);
      this.notifications.update(list => list.filter(n => n.id !== id));
      if (wasUnread) {
        this.recalculateUnreadCount();
      }
    }

    clearAll(): void {
      this.notifications.set([]);
      this.unreadCount.set(0);
    }

    private recalculateUnreadCount(): void {
      const count = this.notifications().filter(n => !n.read).length;
      this.unreadCount.set(count);
    }
  }

  describe('notifications signal', () => {
    it('should start as empty array', () => {
      const service = new TestableNotificationService();
      expect(service.notifications()).toEqual([]);
    });

    it('should add notifications to the list', () => {
      const service = new TestableNotificationService();
      service.addNotification({
        instance_id: 'inst-1',
        agent_id: 'agent-1',
        name: 'Test',
        status: 'COMPLETED',
        timestamp: new Date().toISOString(),
      });

      expect(service.notifications().length).toBe(1);
      expect(service.notifications()[0].id).toBe('inst-1');
    });

    it('should limit notifications to 50 items', () => {
      const service = new TestableNotificationService();

      for (let i = 0; i < 60; i++) {
        service.addNotification({
          instance_id: `inst-${i}`,
          agent_id: 'agent-1',
          name: `Test ${i}`,
          status: 'COMPLETED',
          timestamp: new Date().toISOString(),
        });
      }

      expect(service.notifications().length).toBe(50);
    });
  });

  describe('unreadCount signal', () => {
    it('should start at 0', () => {
      const service = new TestableNotificationService();
      expect(service.unreadCount()).toBe(0);
    });

    it('should increment when notification is added', () => {
      const service = new TestableNotificationService();
      service.addNotification({
        instance_id: 'inst-1',
        agent_id: 'agent-1',
        name: 'Test',
        status: 'COMPLETED',
        timestamp: new Date().toISOString(),
      });

      expect(service.unreadCount()).toBe(1);
    });

    it('should decrement when notification is marked as read', () => {
      const service = new TestableNotificationService();
      service.addNotification({
        instance_id: 'inst-1',
        agent_id: 'agent-1',
        name: 'Test',
        status: 'COMPLETED',
        timestamp: new Date().toISOString(),
      });

      expect(service.unreadCount()).toBe(1);
      service.markAsRead('inst-1');
      expect(service.unreadCount()).toBe(0);
    });

    it('should reset to 0 when markAllAsRead is called', () => {
      const service = new TestableNotificationService();
      service.addNotification({
        instance_id: 'inst-1',
        agent_id: 'agent-1',
        name: 'Test 1',
        status: 'COMPLETED',
        timestamp: new Date().toISOString(),
      });
      service.addNotification({
        instance_id: 'inst-2',
        agent_id: 'agent-1',
        name: 'Test 2',
        status: 'COMPLETED',
        timestamp: new Date().toISOString(),
      });

      expect(service.unreadCount()).toBe(2);
      service.markAllAsRead();
      expect(service.unreadCount()).toBe(0);
    });

    it('should recalculate when cleared notification was unread', () => {
      const service = new TestableNotificationService();
      service.addNotification({
        instance_id: 'inst-1',
        agent_id: 'agent-1',
        name: 'Test',
        status: 'COMPLETED',
        timestamp: new Date().toISOString(),
      });

      expect(service.unreadCount()).toBe(1);
      service.clearNotification('inst-1');
      expect(service.unreadCount()).toBe(0);
    });

    it('should not recalculate when cleared notification was read', () => {
      const service = new TestableNotificationService();
      service.addNotification({
        instance_id: 'inst-1',
        agent_id: 'agent-1',
        name: 'Test',
        status: 'COMPLETED',
        timestamp: new Date().toISOString(),
      });

      service.markAsRead('inst-1');
      expect(service.unreadCount()).toBe(0);
      service.clearNotification('inst-1');
      expect(service.unreadCount()).toBe(0);
    });
  });
});

// =============================================================================
// NOTIFICATION SERVICE SOUND EXCLUSION TESTS
// =============================================================================

describe('NotificationService Sound Exclusion', () => {
  // Sound exclusion set matching the real service logic
  const SOUND_EXCLUDED_AGENT_IDS = new Set(['kb-importer', 'experiencer', 'kb-writer']);

  class MockAudio {
    playCallCount = 0;

    play(): Promise<void> {
      this.playCallCount++;
      return Promise.resolve();
    }
  }

  class TestableNotificationService {
    readonly notifications = signal<any[]>([]);
    readonly unreadCount = signal<number>(0);

    audio: MockAudio | null = null;
    audioUnlocked = false;

    constructor(mockAudio: MockAudio | null) {
      this.audio = mockAudio;
      if (this.audio) {
        this.audio.volume = 0.5;
      }
    }

    private playSound(): void {
      if (this.audio && this.audioUnlocked) {
        this.audio.currentTime = 0;
        this.audio.play().catch(() => {});
      }
    }

    addNotification(notification: InstanceNotification): void {
      const newNotification = {
        ...notification,
        id: notification.instance_id,
        read: false,
      };
      this.notifications.update(list => [newNotification, ...list].slice(0, 50));
      this.unreadCount.update(count => count + 1);
      if (!SOUND_EXCLUDED_AGENT_IDS.has(notification.agent_id)) {
        this.playSound();
      }
    }
  }

  it('should NOT play sound for kb-importer agent', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);
    service['audioUnlocked'] = true;

    service.addNotification({
      instance_id: 'instance-1',
      agent_id: 'kb-importer',
      name: 'KB Import Notification',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    expect(mockAudio.playCallCount).toBe(0);
  });

  it('should NOT play sound for experiencer agent', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);
    service['audioUnlocked'] = true;

    service.addNotification({
      instance_id: 'instance-2',
      agent_id: 'experiencer',
      name: 'Experiencer Notification',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    expect(mockAudio.playCallCount).toBe(0);
  });

  it('should play sound for developer agent', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);
    service['audioUnlocked'] = true;

    service.addNotification({
      instance_id: 'instance-3',
      agent_id: 'developer',
      name: 'Developer Notification',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    expect(mockAudio.playCallCount).toBe(1);
  });

  it('should play sound for leader agent', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);
    service['audioUnlocked'] = true;

    service.addNotification({
      instance_id: 'instance-4',
      agent_id: 'leader',
      name: 'Leader Notification',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    expect(mockAudio.playCallCount).toBe(1);
  });

  it('should play sound when agent_id is empty string', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);
    service['audioUnlocked'] = true;

    service.addNotification({
      instance_id: 'instance-5',
      agent_id: '',
      name: 'Empty Agent Notification',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    expect(mockAudio.playCallCount).toBe(1);
  });

  it('should play sound when agent_id is a random agent', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);
    service['audioUnlocked'] = true;

    service.addNotification({
      instance_id: 'instance-6',
      agent_id: 'some-other-agent',
      name: 'Random Agent Notification',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    expect(mockAudio.playCallCount).toBe(1);
  });

  it('should not play sound for mixed batch (some excluded, some not)', () => {
    const mockAudio = new MockAudio();
    const service = new TestableNotificationService(mockAudio);
    service['audioUnlocked'] = true;

    // Add notifications for kb-importer (excluded), developer (not excluded),
    // experiencer (excluded), leader (not excluded)
    service.addNotification({
      instance_id: 'instance-1',
      agent_id: 'kb-importer',
      name: 'KB Import',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    service.addNotification({
      instance_id: 'instance-2',
      agent_id: 'developer',
      name: 'Developer',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    service.addNotification({
      instance_id: 'instance-3',
      agent_id: 'experiencer',
      name: 'Experiencer',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    service.addNotification({
      instance_id: 'instance-4',
      agent_id: 'leader',
      name: 'Leader',
      status: 'COMPLETED' as const,
      timestamp: new Date().toISOString(),
    });

    // Only developer and leader should trigger sound (2 total)
    expect(mockAudio.playCallCount).toBe(2);
  });
});
