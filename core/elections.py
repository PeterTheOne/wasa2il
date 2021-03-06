import json
import logging
import random
import sys

from pyvotecore.schulze_method import SchulzeMethod as Condorcet
from pyvotecore.schulze_npr import SchulzeNPR as Schulze
from pyvotecore.schulze_stv import SchulzeSTV
from pyvotecore.stv import STV

# This is the old custom Schulze code. For now we continue to use it by
# default, silently comparing with results from pyvotecore (canarying).
import schulze

logger = logging.getLogger(__name__)


class BallotCounter(object):
    """
    This class contains the results of an election, making it easy to
    tally up the results using a few different methods.
    """
    VOTING_SYSTEMS = (
        ('condorcet', 'Condorcet'),
        ('schulze', 'Schulze, Ordered list'),
        ('schulze_old', 'Schulze, Ordered list (old)'),
        ('schulze_new', 'Schulze, Ordered list (new)'),
        ('stcom', 'Steering Committee Election'),
        ('stv1', 'STV, Single winner'),
        ('stv2', 'STV, Two winners'),
        ('stv3', 'STV, Three winners'),
        ('stv4', 'STV, Four winners'),
        ('stv5', 'STV, Five winners'),
        ('stv10', 'STV, Ten winners')
    )

    def __init__(self, ballots=None):
        """
        Ballots should be a list of lists of (rank, candidate) tuples.
        """
        self.ballots = ballots or []
        self.candidates = self.get_candidates()

    def system_name(self, system):
        return [n for m, n in self.VOTING_SYSTEMS if m == system][0]

    def load_ballots(self, filename):
        """Load ballots from disk"""
        with open(filename, 'r') as fd:
            self.ballots += json.load(fd, encoding='utf-8')
        self.candidates = self.get_candidates()
        return self

    def save_ballots(self, filename):
        """Save the ballots to disk.

        Ballots are shuffled before saving, to further anonymize the data
        (user IDs will already have been stripped).
        """
        unicode_ballots = []
        for ballot in self.ballots:
            unicode_ballots.append(
               [(rank, unicode(cand)) for rank, cand in ballot])
        random.shuffle(unicode_ballots)
        if filename == '-':
            json.dump(unicode_ballots, sys.stdout, encoding='utf-8', indent=1)
        else:
            with open(filename, 'w') as fd:
                json.dump(unicode_ballots, fd, encoding='utf-8', indent=1)

    def get_candidates(self):
        candidates = {}
        for ballot in self.ballots:
            for rank, candidate in ballot:
                candidates[candidate] = 1
        return candidates.keys()

    def ballots_as_lists(self):
        for ballot in self.ballots:
            yield([candidate for rank, candidate in sorted(ballot)])

    def ballots_as_rankings(self):
        for ballot in self.ballots:
            rankings = {}
            for rank, candidate in ballot:
                rankings[candidate] = rank
            yield(rankings)

    def hashes_with_counts(self, ballots):
        for ballot in ballots:
            yield {"count": 1, "ballot": ballot}

    def schulze_results_old(self):
        candidates = self.candidates
        preference = schulze.rank_votes(self.ballots, candidates)
        strongest_paths = schulze.compute_strongest_paths(preference, candidates)
        ordered_candidates = schulze.get_ordered_voting_results(strongest_paths)
        return [cand for cand in ordered_candidates]

    def schulze_results_new(self, winners=None):
        if winners is None:
            winners = max(len(b) for b in self.ballots)
        return Schulze(
                list(self.hashes_with_counts(self.ballots_as_rankings())),
                winner_threshold=min(winners, len(self.candidates)),
                ballot_notation=Schulze.BALLOT_NOTATION_RANKING,
            ).as_dict()['order']

    def schulze_results(self, winners=None):
        """Wrapper to canary new schulze code, comparing with old"""
        old_style = self.schulze_results_old()
        new_style = self.schulze_results_new(winners=winners)
        if old_style != new_style:
            logger.warning('Schulze old result does not match schulze new!')
        else:
            logger.info('Schulze old and new match, hooray.')
        return old_style

    def schulze_stv_results(self, winners=None):
        if winners is None:
            winners = 1
        return sorted(list(SchulzeSTV(
                list(self.hashes_with_counts(self.ballots_as_rankings())),
                required_winners=min(winners, len(self.candidates)),
                ballot_notation=Schulze.BALLOT_NOTATION_RANKING,
            ).as_dict()['winners']))

    def stv_results(self, winners=None):
        if winners is None:
            winners = 1
        return list(STV(
                list(self.hashes_with_counts(self.ballots_as_lists())),
                required_winners=winners
            ).as_dict()['winners'])

    def condorcet_results(self):
        result = Condorcet(
                list(self.hashes_with_counts(self.ballots_as_rankings())),
                ballot_notation=Schulze.BALLOT_NOTATION_RANKING,
            ).as_dict()
        if not result.get('tied_winners'):
            return [result['winner']]
        else:
            return []

    def stcom_results(self):
        """Icelandic Pirate party steering committee elections.

        Returns 10 or 11 members; the first five are the steering committee,
        the next five are the deputies (both selected using STV).  The 11th
        is the condorcet winner, if there is one. Note that the 11th result
        will be a duplicate.
        """
        result = self.schulze_results(winners=10)
        stcom = result[:5]
        deputies = result[5:]
        condorcet = self.condorcet_results()
        return sorted(stcom) + sorted(deputies) + condorcet

    def results(self, method, winners=None):
        assert(method in [system for system, name in self.VOTING_SYSTEMS])

        if method == 'schulze':
            return self.schulze_results(winners=winners)

        elif method == 'schulze_old':
            return self.schulze_results_old()

        elif method == 'schulze_new':
            return self.schulze_results_new(winners=winners)

        elif method == 'condorcet':
            return self.condorcet_results()

        elif method == 'stcom':
            return self.stcom_results()

        elif method.startswith('stv'):
            return self.stv_results(winners=int(method[3:] or 1))

        else:
            raise Exception('Invalid voting method: %s' % method)

    def truncate_ballots(self, max_length):
        truncated_ballots = []
        max_seen_length = 0
        for ballot in self.ballots:
            tb = sorted(ballot)[:max_length]
            max_seen_length = max(len(tb), max_seen_length)
            truncated_ballots.append(tb)
        self.ballots = truncated_ballots
        return max_seen_length

    def ballot_length(self):
        maxl = 0
        for ballot in self.ballots:
            maxl = max(maxl, len(ballot))
        return max(1, maxl)

    def minimize_ballots(self, methods, winners=None):
        """
        This will iteratively attempt to shorten the ballots as
        much as possible, without altering the results of the
        election given the named criteria.
        """
        minl = maxl = 1
        maxl = self.ballot_length()

        # Shirt circuit if the ballots are already too short
        if not minl < maxl + 2:
            return maxl

        # Gather the basic results
        results = {}
        for m in methods:
            results[m] = self.results(m, winners=winners)

        if winners is None:
            winners = maxl

        # Binary search for shortest viable ballot length
        # FIXME: This is somewhat inefficient, it would be faster to
        #        check one method at a time.
        while minl < (maxl - 2):
            midp = (minl + maxl) // 2
            tbc = BallotCounter(self.ballots)
            tbc.truncate_ballots(minl + maxl // 2)
            same = True
            for m in methods:
                if (results[m][:winners] !=
                        tbc.results(m, winners=winners)[:winners]):
                    same = False
                    break
            if same:
                maxl = midp
            else:
                minl = midp

        self.ballots = tbc.ballots
        return maxl


if __name__ == "__main__":
    import sys

    # Configure logging
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.DEBUG)

    try:
        operation = sys.argv[1]
        system = sys.argv[2]
        filenames = sys.argv[3:]
    except IndexError:
        print('Usage: %s count <system> <ballot files ...>' % sys.argv[0])
        print('       %s trunc <systems> <ballot files ...>' % sys.argv[0])
        sys.exit(1)

    if ':' in system:
        system, winners = system.split(':')
        winners = int(winners)
    else:
        winners = None

    bc = BallotCounter()
    for fn in filenames:
        bc.load_ballots(fn)

    if operation == 'count':
        print('Voting system:\n\t%s (%s)' % (bc.system_name(system), system))
        print('')
        print('Loaded %d ballots from:\n\t%s' % (len(bc.ballots),
                                                 '\n\t'.join(filenames)))
        print('')
        print('Results:\n\t%s' % ', '.join(bc.results(system,
                                                      winners=winners)))
        print('')

    elif operation.startswith('trunc'):
        systems = [s.strip() for s in system.split(',')]
        org_len = bc.ballot_length()
        max_len = bc.minimize_ballots(systems, winners=winners)
        bc.save_ballots('-')
        sys.stderr.write('Reduced ballots to max length of %s from %s\n'
                         % (max_len, org_len))


else:
    # Suppress errors in case logging isn't configured elsewhere
    logger.addHandler(logging.NullHandler())
