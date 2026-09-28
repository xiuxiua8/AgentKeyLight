import os
import subprocess
import sys
import unittest

from agent_keylight.agents import agent, process, running


class AgentProcessTests(unittest.TestCase):
    def test_reads_this_process(self):
        me = process(os.getpid())
        self.assertEqual(me.parent, os.getppid())
        self.assertTrue(me.name.startswith("python") or me.name.startswith("Python"), me.name)
        self.assertTrue(running(me.pid, me.started))
        self.assertFalse(running(me.pid, me.started + 5))

    def test_unknown_pids_are_not_running(self):
        self.assertIsNone(process(0))
        self.assertFalse(running(2**22 + 7, 0.0))

    def test_skips_shells_to_find_the_agent(self):
        # A shell starts Python, as an agent's hook command may; the agent is this test process.
        code = "from agent_keylight.agents import agent; a = agent(); print(a.pid)"
        output = subprocess.run(
            ["/bin/sh", "-c", f'{sys.executable} -c "{code}"; true'],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(int(output), os.getpid())

    def test_direct_parent_is_the_agent_when_it_is_not_a_shell(self):
        self.assertEqual(agent(os.getpid()).pid, os.getpid())


if __name__ == "__main__":
    unittest.main()
