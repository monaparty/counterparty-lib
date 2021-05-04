from abc import ABCMeta, abstractmethod

class trigger_receiver(metaclass=ABCMeta):

    @classmethod
    @abstractmethod
    def initialise (cls, db):
        pass

    @abstractmethod
    def __init__(self, db, source, target_hash, payload_bytes):
        pass

    @property
    @abstractmethod
    def name(self):
        pass

    @classmethod
    @abstractmethod
    def compose(cls, db, source, target_hash, payload, payload_is_hex):
        pass

    @classmethod
    @abstractmethod
    def target_table_name (cls):
        pass

    @abstractmethod
    def validate(self):
        pass

    @abstractmethod
    def execute(self, tx):
        pass

