import { CommandRegistryService } from './command-registry.service';

/**
 * Logic-mirror specs for the client-side command registry (phase2-plan.md
 * Task 1 acceptance). Plain TS, NO TestBed. Parse rules mirror the backend
 * ``parse_slash_command`` (O-B1: ``//`` checked BEFORE ``/``; name
 * case-insensitive; ``/`` with empty body is not a command).
 */
describe('CommandRegistryService', () => {
  let service: CommandRegistryService;

  beforeEach(() => {
    service = new CommandRegistryService();
  });

  describe('parseCommandInput — command outcomes', () => {
    it("'/compact' → known command", () => {
      const outcome = service.parseCommandInput('/compact');
      expect(outcome).toEqual({
        known: true,
        def: expect.objectContaining({ name: 'compact' }),
      });
    });

    it("'/COMPACT' → known (case-insensitive, mirrors BE name.lower())", () => {
      const outcome = service.parseCommandInput('/COMPACT');
      expect('known' in outcome && outcome.known).toBe(true);
    });

    it("'/compact  with args' → still the known command", () => {
      const outcome = service.parseCommandInput('/compact   force please');
      expect('known' in outcome && outcome.known).toBe(true);
      if ('known' in outcome && outcome.known) {
        expect(outcome.def.name).toBe('compact');
      }
    });

    it("'/foo' → leading slash but unknown (advisory; BE 400 stays authoritative)", () => {
      const outcome = service.parseCommandInput('/foo');
      expect(outcome).toEqual({ known: false, name: 'foo' });
    });
  });

  describe('parseCommandInput — escape (O-B1: // checked BEFORE /)', () => {
    it("'//compact is useful' → escape delivering '/compact is useful' verbatim", () => {
      const outcome = service.parseCommandInput('//compact is useful');
      expect(outcome).toEqual({ escape: true, text: '/compact is useful' });
    });

    it("'//etc/hosts' → escape delivering '/etc/hosts' (one slash stripped)", () => {
      const outcome = service.parseCommandInput('//etc/hosts');
      expect(outcome).toEqual({ escape: true, text: '/etc/hosts' });
    });

    it("'//' alone → escape delivering '/' (nothing command-ish remains)", () => {
      const outcome = service.parseCommandInput('//');
      expect(outcome).toEqual({ escape: true, text: '/' });
    });
  });

  describe('parseCommandInput — plain text', () => {
    it.each(["'hello world'", 'hello', '', '   ', '/', '  /whitespace-prefixed'])(
      '%s → not a command',
      (content) => {
        const outcome = service.parseCommandInput(content);
        expect(outcome).toEqual({ isCommand: false });
      },
    );
  });

  describe('registry extension is a ONE-ENTRY change', () => {
    it('registering a new command makes it parseable', () => {
      expect(service.parseCommandInput('/clear')).toEqual({ known: false, name: 'clear' });
      service.registerCommand({ name: 'clear', description: 'Clear the context' });
      const outcome = service.parseCommandInput('/clear');
      expect('known' in outcome && outcome.known).toBe(true);
      expect(service.hasCommand('clear')).toBe(true);
    });

    it('duplicate registration is ignored (first wins)', () => {
      service.registerCommand({ name: 'compact', description: 'SECOND COPY' });
      const compact = service.commands().find(c => c.name === 'compact');
      expect(compact?.description).not.toBe('SECOND COPY');
      expect(service.commands().filter(c => c.name === 'compact')).toHaveLength(1);
    });

    it('seeded registry exposes compact with a description', () => {
      const compact = service.commands().find(c => c.name === 'compact');
      expect(compact).toBeDefined();
      expect(compact?.description.length).toBeGreaterThan(0);
    });
  });
});
